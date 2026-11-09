"""JurisMT: extração, armazenamento e recuperação local de decisões de TJMT e TRT-23.

A edição 35 é um PDF oficial do TJMT, não um espelho de todo o acervo.
Este módulo NÃO acessa endpoints privados nem contorna proteções.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

import fitz
import numpy as np

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "jurismt.sqlite"
UPLOADS = ROOT / "data" / "uploads"
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
OFFICIAL_EDITION_35 = (
    "https://cms-midia.tjmt.jus.br/dsaportaltjmt/biblioteca-midia/"
    "108b0000-0aa5-0a58-2eca-08df3a7fe147/"
    "108b0000-0aa5-0a58-2fd3-08df3a7fe147.pdf"
)

PROCESS_NUMBER = r"\d{7}-\d{2}\.\d{4}\.8\.11\.\d{4}"
TRT23_PROCESS_NUMBER = re.compile(r"(?<!\d)\d{7}-\d{2}\.\d{4}\.5\.23\.\d{4}(?!\d)")
# O rodapé de cada ementa do Ementário do TJMT contém esta referência.
FOOTNOTE = re.compile(
    rf"\(\s*TJMT\s*,\s*(?P<classe>.{{1,180}}?)\s+(?P<numero>{PROCESS_NUMBER})\s*,\s*"
    rf"Relator\s*\(a\)\s*(?P<relator>.{{1,160}}?)\s*,\s*(?P<orgao>.{{1,160}}?)\s*,\s*"
    rf"Julgado\s+em\s*(?P<julgamento>\d{{2}}/\d{{2}}/\d{{4}})\s*,\s*"
    rf"Publicado\s+em\s*(?P<publicacao>\d{{2}}/\d{{2}}/\d{{4}})\s*\)",
    flags=re.I | re.S,
)
AREAS = ("Direito Privado", "Direito Público", "Direito Criminal", "Direito Penal", "Direito Cível", "Direito Civil")
HEADER = re.compile(r"^(?:Emitido às .*|EDIÇÃO Nº .*|Página \d+ de \d+|PODER JUDICIÁRIO.*|JURISPRUDÊNCIA CÍVIL E CRIMINAL.*)$", re.I)
STOPWORDS = {"sobre", "qual", "quais", "para", "com", "sem", "dos", "das", "nas", "nos", "uma", "uns", "por", "que", "como", "quando", "pela", "pelo", "entre", "onde", "mais", "depois", "essa", "esse", "esta", "este", "tem", "ser", "foi", "são", "seu", "sua", "jurisprudencia", "jurisprudência", "decisões", "decisão", "tjmt"}


ALLOWED_HOSTS = {
    "TJMT": {"cms-midia.tjmt.jus.br", "jurisprudencia.tjmt.jus.br", "www.tjmt.jus.br", "tjmt.jus.br"},
    "TRT-23": {"portal.trt23.jus.br", "www.trt23.jus.br", "trt23.jus.br", "pje.trt23.jus.br", "jurisprudencia.jt.jus.br"},
}


def source_link_allowed(url: str, tribunal: str | None = None) -> bool:
    """Aceita somente URLs HTTPS de fontes oficiais, conforme o tribunal."""
    try:
        parsed = urlparse(url)
        domains = ALLOWED_HOSTS.get(tribunal, set().union(*ALLOWED_HOSTS.values()))
        return parsed.scheme == "https" and parsed.hostname in domains and not parsed.username and not parsed.password
    except Exception:
        return False


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        sha256 TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        ementa TEXT NOT NULL,
        numero TEXT NOT NULL,
        classe TEXT,
        relator TEXT,
        orgao TEXT,
        area TEXT,
        julgamento TEXT,
        publicacao TEXT,
        edicao TEXT,
        source_url TEXT,
        file_sha256 TEXT,
        pagina_inicio INTEGER,
        pagina_fim INTEGER,
        imported_at TEXT NOT NULL,
        tribunal TEXT NOT NULL DEFAULT 'TJMT'
    );
    CREATE TABLE IF NOT EXISTS segments (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        part_idx INTEGER NOT NULL,
        content TEXT NOT NULL,
        embedding BLOB,
        embedding_model TEXT,
        UNIQUE(document_id, part_idx)
    );
    CREATE INDEX IF NOT EXISTS idx_docs_filtros ON documents(area, julgamento, relator, orgao);
    CREATE INDEX IF NOT EXISTS idx_segment_doc ON segments(document_id);
    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        title, ementa, numero, content='documents', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON documents BEGIN
      INSERT INTO documents_fts(rowid,title,ementa,numero) VALUES(new.id,new.title,new.ementa,new.numero);
    END;
    CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON documents BEGIN
      INSERT INTO documents_fts(documents_fts,rowid,title,ementa,numero) VALUES('delete',old.id,old.title,old.ementa,old.numero);
    END;
    CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON documents BEGIN
      INSERT INTO documents_fts(documents_fts,rowid,title,ementa,numero) VALUES('delete',old.id,old.title,old.ementa,old.numero);
      INSERT INTO documents_fts(rowid,title,ementa,numero) VALUES(new.id,new.title,new.ementa,new.numero);
    END;
    """)
    # Migração não destrutiva: bancos da versão 1.0 são identificados como TJMT.
    columns = {col[1] for col in conn.execute("PRAGMA table_info(documents)")}
    if "tribunal" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN tribunal TEXT NOT NULL DEFAULT 'TJMT'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_tribunal ON documents(tribunal, julgamento)")
    conn.commit()
    return conn


def _iso_date(value: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def _clean_line(line: str) -> str:
    line = line.strip()
    if HEADER.match(line) or re.fullmatch(r"(?:\d{1,3}|Página \d+ de \d+)", line):
        return ""
    return line


def _page_text(page: fitz.Page) -> str:
    # Utilizar ordem de leitura e retirar cabeçalhos/rodapés repetidos.
    lines = [s for x in page.get_text("text", sort=True).splitlines() if (s := _clean_line(x))]
    return "\n".join(lines)


def _get_area(prefix: str, previous: str) -> str:
    choices = list(re.finditer(r"(?im)^\s*(Direito Privado|Direito Público|Direito Criminal|Direito Penal|Direito Cível|Direito Civil)\s*$", prefix))
    return choices[-1].group(1).strip() if choices else previous


def _extract_heading(raw: str) -> tuple[str, str]:
    # O título normalmente é o primeiro bloco em maiúsculas, seguido da ementa.
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    while lines and (lines[0] in AREAS or lines[0].casefold() == "direito privado"):
        lines.pop(0)
    title_lines = []
    for line in lines:
        letters = [c for c in line if c.isalpha()]
        upper = sum(c.isupper() for c in letters) / max(len(letters), 1)
        if upper > 0.88 and not line.startswith("(TJMT") and len(title_lines) < 8:
            title_lines.append(line)
            # Título editorial termina com ponto; o bloco seguinte é ementa.
            if line.endswith("."):
                break
        else:
            break
    # Título de julgados costuma começar com "DIREITO ... - ...".
    if title_lines and re.match(r"^(DIREITO|RESPONSABILIDADE|AÇÃO|AGRAVO|APELAÇÃO|HABEAS)", title_lines[0], re.I):
        title = " ".join(title_lines)[:350]
        body = "\n".join(lines[len(title_lines):]).strip()
    else:
        title = "Ementa do TJMT"
        body = "\n".join(lines).strip()
    return title, body


def extract_ementario(pdf_bytes: bytes, edition: str = "35/2026") -> list[dict[str, Any]]:
    """Extrai julgados da estrutura editorial do Ementário; falha sem inventar dados.

    Suporta ementas quebradas entre páginas. PDF escaneado exige OCR externo
    (não implementado aqui). Este parser é específico, não universal.
    """
    if len(pdf_bytes) > 55 * 1024 * 1024:
        raise ValueError("PDF excede 55 MB: processamento recusado.")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Arquivo não parece ser um PDF válido.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if doc.page_count > 350:
            raise ValueError("PDF com mais de 350 páginas: limite de segurança excedido.")
        content: list[str] = []
        offsets: list[tuple[int, int]] = []
        pos = 0
        for i, page in enumerate(doc):
            text = _page_text(page) + "\n"
            content.append(text)
            offsets.append((pos, i+1))
            pos += len(text)
        full = "".join(content)
        results = []
        first_section = re.search(r"(?im)^\s*(?:Direito Privado|Direito Público|Direito Criminal|Direito Penal|Direito Cível|Direito Civil)\s*$", full)
        last = first_section.start() if first_section else 0
        area = "Não identificada"
        file_hash = hashlib.sha256(pdf_bytes).hexdigest()
        matches = list(FOOTNOTE.finditer(full))
        for match in matches:
            raw = full[last:match.start()]
            area = _get_area(raw, area)
            title, body = _extract_heading(raw)
            # Ignorar capa/sumário antes da primeira ocorrência.
            if len(body) < 70:
                last = match.end()
                continue
            meta = {key: " ".join(value.split()) for key, value in match.groupdict().items()}
            # Paginação 1-based para links com #page=N.
            title_start = last
            if title != "Ementa do TJMT":
                ix = raw.find(title.split()[0])
                if ix >= 0:
                    title_start += ix
            p_start = 1
            p_end = 1
            for offset, page in offsets:
                if offset <= title_start: p_start = page
                if offset <= match.start(): p_end = page
            results.append({
                "title": title,
                "ementa": body,
                "numero": meta["numero"],
                "classe": meta["classe"],
                "relator": meta["relator"],
                "orgao": meta["orgao"],
                "julgamento": _iso_date(meta["julgamento"]),
                "publicacao": _iso_date(meta["publicacao"]),
                "area": area,
                "edicao": edition,
                "pagina_inicio": p_start,
                "pagina_fim": p_end,
                "file_sha256": file_hash,
            })
            last = match.end()
        return results
    finally:
        doc.close()


def chunks_of(text: str, words_per_chunk: int = 155, overlap: int = 35) -> list[str]:
    # Sobreposição ajuda a preservar contexto ao cortar as ementas longas.
    words = text.split()
    if not words: return []
    chunks = []
    step = words_per_chunk - overlap
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start:start + words_per_chunk]))
        if start + words_per_chunk >= len(words): break
    return chunks


def import_pdf(conn: sqlite3.Connection, pdf_bytes: bytes, edition: str, source_url: str = "") -> dict[str,int]:
    parsed = extract_ementario(pdf_bytes, edition)
    if not parsed:
        return {"found": 0, "inserted": 0, "duplicates": 0}
    if source_url and not source_link_allowed(source_url, "TJMT"):
        raise ValueError("URL de origem fora da lista de domínios oficiais do TJMT.")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    dest = UPLOADS / (file_hash + ".pdf")
    if not dest.exists(): dest.write_bytes(pdf_bytes)
    n = 0
    for item in parsed:
        text = item["title"] + "\n" + item["ementa"]
        fingerprint = hashlib.sha256((item["numero"]+"\n"+text).encode()).hexdigest()
        cursor = conn.execute("""INSERT OR IGNORE INTO documents
            (sha256,title,ementa,numero,classe,relator,orgao,area,julgamento,publicacao,
             edicao,source_url,file_sha256,pagina_inicio,pagina_fim,imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fingerprint, item["title"], item["ementa"], item["numero"],
             item["classe"],item["relator"],item["orgao"],item["area"],item["julgamento"],
             item["publicacao"],item["edicao"], source_url,item["file_sha256"],
             item["pagina_inicio"],item["pagina_fim"],datetime.now(timezone.utc).isoformat()))
        if cursor.rowcount:
            n += 1
            for idx, chunk in enumerate(chunks_of(text)):
                conn.execute("INSERT INTO segments(document_id,part_idx,content) VALUES (?,?,?)",
                             (cursor.lastrowid, idx, chunk))
    conn.commit()
    return {"found":len(parsed), "inserted": n, "duplicates":len(parsed)-n}


def filters_available(conn:sqlite3.Connection) -> dict[str,list[str]]:
    names = ["area", "relator", "orgao", "classe", "tribunal"]
    return {field:[row[0] for row in conn.execute(
        f"SELECT DISTINCT {field} FROM documents WHERE {field} IS NOT NULL AND {field}<>'' ORDER BY {field}")]
        for field in names}


def build_filter_sql(filters: dict[str,str]) -> tuple[str,list[Any]]:
    parts = []
    args = []
    for key in ("area", "relator", "orgao", "classe", "tribunal"):
        if filters.get(key) and filters[key] != "Todas":
            parts.append(f"d.{key}=?")
            args.append(filters[key])
    if filters.get("start_date"):
        parts.append("d.julgamento>=?")
        args.append(filters["start_date"])
    if filters.get("end_date"):
        parts.append("d.julgamento<=?")
        args.append(filters["end_date"])
    if filters.get("numero"):
        parts.append("d.numero LIKE ?")
        args.append("%" + filters["numero"].strip()[:50] + "%")
    return " AND ".join(parts) if parts else "1=1", args


def index_missing_segments(conn: sqlite3.Connection, model: Any, progress=None, batch_size=32) -> int:
    pending = conn.execute("SELECT id,content FROM segments WHERE embedding IS NULL OR embedding_model<>?",
                           (MODEL_NAME,)).fetchall()
    n = 0
    for start in range(0,len(pending),batch_size):
        batch = pending[start:start+batch_size]
        vectors = model.encode([b["content"] for b in batch], normalize_embeddings=True,
                               show_progress_bar=False, batch_size=16, convert_to_numpy=True)
        for row, vector in zip(batch, vectors):
            conn.execute("UPDATE segments SET embedding=?,embedding_model=? WHERE id=?",
                         (np.asarray(vector,dtype=np.float32).tobytes(), MODEL_NAME,row["id"]))
            n += 1
        conn.commit()
        if progress: progress(n, len(pending))
    return n


def search(conn:sqlite3.Connection, query:str, filters:dict[str,str], model:Any=None,
           exclude:str="", limit:int=20) -> list[dict[str,Any]]:
    where, args = build_filter_sql(filters)
    rows = conn.execute(f"SELECT d.* FROM documents d WHERE {where}", args).fetchall()
    excluded = [x.casefold().strip() for x in exclude.split(",") if x.strip()]
    rows = [r for r in rows if not any(term in (r["title"]+" "+r["ementa"]).casefold() for term in excluded)]
    if not rows: return []
    ids = {r["id"] for r in rows}
    text_query = query.strip()
    lexical: dict[int,float] = {}
    if text_query:
        words = [t for t in re.findall(r"[^\W_]{3,}", text_query.lower(), flags=re.U)
                 if t not in STOPWORDS][:18]
        if words:
            expression = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))
            try:
                hits = conn.execute("SELECT rowid,bm25(documents_fts) AS score FROM documents_fts WHERE documents_fts MATCH ? ORDER BY score LIMIT 2000",(expression,)).fetchall()
                for rank, h in enumerate(hits):
                    if h["rowid"] in ids: lexical[h["rowid"]] = 1.0/(1.0 + rank/15.0)
            except sqlite3.OperationalError:
                pass
    semantic:dict[int,tuple[float,str]]={}
    if model is not None and text_query:
        query_vec = np.asarray(model.encode([text_query], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)[0], dtype=np.float32)
        seg_rows = conn.execute("SELECT document_id,content,embedding FROM segments WHERE embedding IS NOT NULL AND embedding_model=?", (MODEL_NAME,)).fetchall()
        # Somente segmentos da seleção atual de filtros.
        indexed = [(s["document_id"], s["content"], np.frombuffer(s["embedding"], dtype=np.float32))
                   for s in seg_rows if s["document_id"] in ids]
        if indexed:
            matrix = np.vstack([x[2] for x in indexed])
            if matrix.shape[1] == query_vec.shape[0]:
                scores = matrix @ query_vec
                for i, score in enumerate(scores):
                    doc_id, excerpt, _ = indexed[i]
                    if doc_id not in semantic or float(score) > semantic[doc_id][0]:
                        semantic[doc_id] = (float(score),excerpt)
    found = []
    # Sem consulta, mostrar registros por data. Com consulta e sem match, não mostrar dados aleatórios.
    for r in rows:
        doc_id = r["id"]
        if text_query and doc_id not in semantic and doc_id not in lexical:
            continue
        if model is not None and semantic:
            # Relevância relativa, não probabilidade nem avaliação jurídica.
            score = 0.86 * max(0.0, semantic.get(doc_id,(0.0,""))[0]) + 0.14 * lexical.get(doc_id,0.0)
        else:
            score = lexical.get(doc_id,0.0)
        item = dict(r)
        item["rank_score"] = round(float(score),4)
        item["excerpt"] = semantic.get(doc_id,(0.0,r["ementa"][:900]))[1]
        found.append(item)
    if text_query:
        found.sort(key=lambda x:(x["rank_score"],x["julgamento"] or ""), reverse=True)
    else:
        found.sort(key=lambda x:x["julgamento"] or "", reverse=True)
    return found[:limit]


def stats(conn:sqlite3.Connection)->dict[str,int]:
    return {
        "tjmt": conn.execute("SELECT count(*) FROM documents WHERE tribunal='TJMT'").fetchone()[0],
        "trt23": conn.execute("SELECT count(*) FROM documents WHERE tribunal='TRT-23'").fetchone()[0],
        "documents":conn.execute("SELECT count(*) FROM documents").fetchone()[0],
        "segments":conn.execute("SELECT count(*) FROM segments").fetchone()[0],
        "pending":conn.execute("SELECT count(*) FROM segments WHERE embedding IS NULL OR embedding_model<>?",(MODEL_NAME,)).fetchone()[0],
    }


def _trt23_date(value: str) -> str:
    """Datas de entrada manual no formato AAAA-MM-DD ou DD/MM/AAAA."""
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Data inválida: {value}. Use DD/MM/AAAA.")


def extract_trt23_document(pdf_bytes: bytes, metadata: dict[str, str] | None = None) -> dict[str, Any]:
    """Lê UM acórdão/decisão trabalhista por PDF, sem presumir formato fixo do PJe.

    O operador confirma os metadados na interface. Números adicionais podem
    corresponder a precedentes citados, não a julgados do próprio documento.
    """
    metadata = metadata or {}
    if len(pdf_bytes) > 55 * 1024 * 1024 or not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Envie um PDF válido de no máximo 55 MB.")
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if not 1 <= pdf.page_count <= 350:
            raise ValueError("São permitidos PDFs de 1 a 350 páginas.")
        pages = [page.get_text("text", sort=True).strip() for page in pdf]
        body = "\n\n".join(x for x in pages if x).strip()
        if len(body) < 120:
            raise ValueError("Não foi possível extrair texto. PDF digitalizado pode precisar de OCR.")
        detected = list(dict.fromkeys(TRT23_PROCESS_NUMBER.findall(body)))
        number = (metadata.get("numero") or "").strip()
        if number and not TRT23_PROCESS_NUMBER.fullmatch(number):
            raise ValueError("O processo deve ter formato CNJ da Justiça do Trabalho em MT (N.NNNNNNN-NN.AAAA.5.23.NNNN).")
        if not number:
            if len(detected) != 1:
                raise ValueError(
                    "Identificação ambígua do processo no PDF. Preencha o número CNJ do julgamento "
                    "(pode haver outros processos citados no texto)."
                )
            number = detected[0]
        # Não inferir dados do julgador de trechos em que terceiros podem ser citados.
        title = (metadata.get("titulo") or f"Decisão do TRT-23 — processo {number}").strip()[:350]
        return {
            "title": title, "ementa": body, "numero": number,
            "classe": (metadata.get("classe") or "").strip()[:180],
            "relator": (metadata.get("relator") or "").strip()[:180],
            "orgao": (metadata.get("orgao") or "").strip()[:180],
            "area": "Direito do Trabalho",
            "julgamento": _trt23_date(metadata.get("julgamento", "")),
            "publicacao": _trt23_date(metadata.get("publicacao", "")),
            "edicao": "Documento individual TRT-23",
            "pagina_inicio": 1, "pagina_fim": pdf.page_count,
            "file_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "referencias_detectadas": detected,
        }
    finally:
        pdf.close()


def import_trt23_pdf(conn: sqlite3.Connection, pdf_bytes: bytes,
                     source_url: str = "", metadata: dict[str, str] | None = None) -> dict[str, int]:
    """Importa manualmente um PDF público do TRT-23: UM PDF = UMA decisão.

    Não consulta nem descarrega dados do portal do tribunal automaticamente.
    """
    if source_url and not source_link_allowed(source_url, "TRT-23"):
        raise ValueError("A URL de origem deve ser HTTPS e de domínio oficial do TRT-23 ou Falcão.")
    item = extract_trt23_document(pdf_bytes, metadata)
    fingerprint = hashlib.sha256(("TRT-23\n"+item["numero"]+"\n"+item["ementa"]).encode()).hexdigest()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / (item["file_sha256"] + ".pdf")
    if not dest.exists():
        dest.write_bytes(pdf_bytes)
    cur = conn.execute("""INSERT OR IGNORE INTO documents
       (sha256,title,ementa,numero,classe,relator,orgao,area,julgamento,publicacao,
        edicao,source_url,file_sha256,pagina_inicio,pagina_fim,imported_at,tribunal)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
       (fingerprint,item["title"],item["ementa"],item["numero"],item["classe"],
        item["relator"],item["orgao"],item["area"],item["julgamento"],
        item["publicacao"],item["edicao"],source_url,item["file_sha256"],
        item["pagina_inicio"],item["pagina_fim"],datetime.now(timezone.utc).isoformat(),"TRT-23"))
    if cur.rowcount:
        for i, chunk in enumerate(chunks_of(item["title"] + "\n" + item["ementa"])):
            conn.execute("INSERT INTO segments(document_id,part_idx,content) VALUES (?,?,?)",
                         (cur.lastrowid, i, chunk))
    conn.commit()
    return {"found": 1, "inserted": int(bool(cur.rowcount)), "duplicates": 1-int(bool(cur.rowcount))}
