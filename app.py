"""Portal privado JurisMT TJMT+TRT-23 — execução: streamlit run app.py --server.address 127.0.0.1"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import hashlib

import requests
import streamlit as st

from engine import (
    DB_PATH, MODEL_NAME, OFFICIAL_EDITION_35, UPLOADS, connect, filters_available,
    import_pdf, import_trt23_pdf, index_missing_segments, search, source_link_allowed, stats,
    update_document_metadata,
)

st.set_page_config(page_title="JurisMT | Pesquisa jurisprudencial", page_icon="⚖️",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
.block-container {padding-top: 1.8rem; max-width: 1320px;}
.hero {background: linear-gradient(115deg,#132a42,#204764); color:#fff; border-radius:14px; padding:22px 26px; margin: 0 0 20px;}
.hero h1{font-size:2rem; margin:0 0 4px;color:#fff;}
.hero p{margin:0;opacity:.88;}
.smallnote{font-size:.86rem;color:#68798b;}
.hit{border-left:3px solid #2c719b; padding-left:12px; margin-top:6px;}
</style>
""", unsafe_allow_html=True)

@st.cache_resource(show_spinner="Carregando o modelo de IA no computador (a primeira instalação requer internet)...")
def load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME, device="cpu")


def download_public_pdf() -> bytes:
    # Uma URL conhecida, pública, citada pela comunicação do TJMT. Sem crawling.
    response = requests.get(OFFICIAL_EDITION_35, timeout=(10, 90), allow_redirects=False,
                            stream=True, headers={"User-Agent": "JurisMT-MVP/1.0 (importacao manual de PDF publico)"})
    try:
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("O link foi redirecionado. Faça o download manual do PDF oficial.")
        output = bytearray()
        for part in response.iter_content(64 * 1024):
            output.extend(part)
            if len(output) > 55 * 1024 * 1024:
                raise ValueError("PDF excede 55 MB.")
        if not output.startswith(b"%PDF-"):
            raise ValueError("A resposta não era um PDF. Importe o documento manualmente.")
        return bytes(output)
    finally:
        response.close()


def safe_date(iso_date: str) -> str:
    try:
        return date.fromisoformat(iso_date).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return "Não informado"


st.markdown('''<div class="hero"><h1>⚖️ JurisMT</h1><p>Pesquisa inteligente em decisões públicas do TJMT e TRT-23 · protótipo privado</p></div>''', unsafe_allow_html=True)
conn = connect(DB_PATH)

with st.sidebar:
    st.subheader("Acervo de jurisprudências")
    metrics = stats(conn)
    a,b = st.columns(2)
    a.metric("Decisões",metrics["documents"])
    b.metric("Trechos",metrics["segments"])
    st.caption(f"TJMT: **{metrics['tjmt']}** · TRT-23: **{metrics['trt23']}**")
    st.caption("O acervo é local e contém apenas os documentos importados.")
    with st.expander("1. Importar documentos", expanded=not bool(metrics["documents"])):
        st.caption("Importação individual sob demanda. O sistema não rastreia automaticamente os portais dos tribunais.")
        st.link_button("Abrir 35º Ementário oficial", OFFICIAL_EDITION_35, use_container_width=True)
        if st.button("Importar PDF oficial da edição 35", use_container_width=True):
            try:
                with st.spinner("Obtendo e extraindo ementas públicas da edição 35..."):
                    data = download_public_pdf()
                    result = import_pdf(conn, data, "35/2026", OFFICIAL_EDITION_35)
                if result["found"]:
                    st.success(f"{result['inserted']} novas ementas importadas; {result['duplicates']} já existiam.")
                else:
                    st.warning("O PDF foi recebido, mas o extrator não identificou os rodapés de julgados.")
                st.rerun()
            except Exception as exc:
                st.error(f"Importação pela internet indisponível: {exc}")
                st.info("Baixe o PDF pelo link acima e use o envio de arquivo abaixo.")
        st.divider()
        st.markdown("**Importação de arquivos obtidos de fontes oficiais**")
        kind = st.radio("Origem do PDF", ["TJMT — Ementário Eletrônico", "TRT-23 — Acórdão ou decisão individual"], key="import_origin")
        if kind.startswith("TRT-23"):
            st.link_button("Abrir pesquisa TRT-23", "https://pje.trt23.jus.br/jurisprudencia/", use_container_width=True)
            st.link_button("Abrir Sistema Falcão", "https://jurisprudencia.jt.jus.br/jurisprudencia-nacional/home", use_container_width=True)
            st.caption("Baixe manualmente UM acórdão/decisão público por PDF. Informe o processo correto e confira os metadados no documento.")
        uploaded = st.file_uploader("Enviar PDF público", type=["pdf"], key="upload_pdf", help="Máximo de 55 MB. TRT-23: um julgamento por PDF; TJMT: Ementário Eletrônico.")
        if kind.startswith("TJMT"):
            edition = st.text_input("Identificação da edição", value="35/2026")
            official_url = st.text_input("URL oficial do PDF (opcional)", placeholder="https://cms-midia.tjmt.jus.br/...", key="url_tj")
            metadata = None
            tribunal = "TJMT"
        else:
            tribunal = "TRT-23"
            official_url = st.text_input("URL oficial do documento (opcional)", placeholder="https://pje.trt23.jus.br/...", key="url_trt")
            edition = ""
            numero = st.text_input("Número do processo CNJ (recomendado)", placeholder="0000123-57.2025.5.23.0001", key="num_trt")
            confirmar_divergente = st.checkbox(
                "Confirmei manualmente um número diferente dos identificados no texto do PDF",
                value=False, key="confirmar_numero_divergente",
                help="Somente marque após verificar visualmente a decisão original. Não valida a existência do processo.")
            with st.expander("Metadados opcionais para filtros"):
                titulo = st.text_input("Título da decisão", key="titulo_trt")
                classe_meta = st.text_input("Classe judicial", key="classe_trt")
                relator_meta = st.text_input("Relator(a)", key="relator_trt")
                orgao_meta = st.text_input("Órgão julgador", key="orgao_trt")
                julg_meta = st.text_input("Data de julgamento (DD/MM/AAAA)", key="julg_trt")
                pub_meta = st.text_input("Data de publicação (DD/MM/AAAA)", key="pub_trt")
            metadata = {
                "numero": numero,
                "confirmar_numero_divergente": confirmar_divergente,
                "titulo": st.session_state.get("titulo_trt", ""),
                "classe": st.session_state.get("classe_trt", ""),
                "relator": st.session_state.get("relator_trt", ""),
                "orgao": st.session_state.get("orgao_trt", ""),
                "julgamento": st.session_state.get("julg_trt", ""),
                "publicacao": st.session_state.get("pub_trt", ""),
            }
        if st.button("Processar PDF enviado", disabled=uploaded is None, use_container_width=True):
            try:
                if official_url and not source_link_allowed(official_url.strip(), tribunal):
                    raise ValueError("Informe uma URL HTTPS de domínio oficial do tribunal selecionado ou deixe o campo vazio.")
                if tribunal == "TJMT":
                    result = import_pdf(conn, uploaded.getvalue(), edition.strip()[:45], official_url.strip())
                else:
                    result = import_trt23_pdf(conn, uploaded.getvalue(), official_url.strip(), metadata)
                if result["found"]:
                    st.success(f"{result['inserted']} nova(s) decisão(ões); {result['duplicates']} já cadastrada(s).")
                    st.rerun()
                else:
                    st.warning("Nenhum julgado identificado. Para TJMT, o extrator espera o padrão do Ementário. Não há OCR.")
            except Exception as exc:
                st.error(f"Não foi possível importar: {exc}")

    with st.expander("2. Preparar a busca semântica", expanded=bool(metrics["documents"] and metrics["pending"])):
        st.caption(f"Modelo gratuito no próprio PC: `{MODEL_NAME}`. Primeiro uso exige download do modelo; as consultas ficam locais.")
        st.write(f"Trechos sem vetores de IA: **{metrics['pending']}**")
        if st.button("Gerar / atualizar índice de IA", disabled=metrics["pending"]==0, use_container_width=True):
            try:
                model = load_model()
                progress = st.progress(0.0)
                with st.spinner("Gerando vetores semânticos localmente..."):
                    indexed = index_missing_segments(conn, model, progress=lambda n,total: progress.progress(n / max(total,1)))
                st.success(f"{indexed} trechos indexados.")
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível carregar o modelo ou criar o índice: {exc}")
                st.info("Instale as dependências do requirements.txt e permita o download inicial do modelo.")
    st.divider()
    st.caption("**Privacidade:** local em 127.0.0.1; sem API de IA externa. Não exponha o Streamlit diretamente à internet.")
    st.caption("**Limites:** somente PDFs públicos, sem rastreamento em massa; resultados dependem da cobertura local e não certificam a atualidade jurídica.")

st.subheader("Encontre decisões pelo contexto jurídico")
st.caption("Descreva os fatos, a questão jurídica ou a tese. Pesquise TJMT, TRT-23 ou os dois, desde que estejam no acervo importado.")
example = "Responsabilidade civil por inscrição indevida em cadastro de inadimplentes e pedido de dano moral"
query = st.text_area("Situação jurídica / tese pesquisada", value=st.session_state.get("query_value",example),
                     height=115, max_chars=3000, placeholder=example)

options = filters_available(conn)
with st.expander("Filtros avançados", expanded=False):
    c1,c2 = st.columns(2)
    with c1:
        tribunal_filter = st.selectbox("Tribunal", ["Todas", "TJMT", "TRT-23"])
        area = st.selectbox("Área", ["Todas", *options["area"]])
        orgao = st.selectbox("Órgão julgador", ["Todas", *options["orgao"]])
        start_date = st.date_input("Julgamentos a partir de", value=None, format="DD/MM/YYYY")
    with c2:
        relator = st.selectbox("Relator(a)", ["Todas", *options["relator"]])
        classe = st.selectbox("Classe processual", ["Todas", *options["classe"]])
        end_date = st.date_input("Julgamentos até", value=None, format="DD/MM/YYYY")
    c3,c4 = st.columns(2)
    with c3: numero = st.text_input("Número do processo (opcional)")
    with c4: exclude = st.text_input("Excluir expressões (separadas por vírgula)")
    min_similarity = st.slider("Similaridade semântica mínima (experimental)", min_value=0.20,
                               max_value=0.80, value=0.40, step=0.05,
                               help="Limiar heurístico de cosseno; ajuste conforme os falsos positivos e os resultados perdidos. Termos textuais correspondentes ainda podem aparecer.")

search_col, count_col = st.columns([1,3])
with search_col:
    do_search = st.button("🔎 Pesquisar com IA", type="primary", use_container_width=True)
with count_col:
    st.caption("Os resultados exibem trechos originais; a pontuação é apenas uma métrica de ordenação, não uma conclusão jurídica.")

if not stats(conn)["documents"]:
    st.info("Primeiro passo: importe um Ementário do TJMT ou um acórdão individual público do TRT-23 pela barra lateral. Depois, clique em 'Gerar / atualizar índice de IA'.")
elif do_search:
    if start_date and end_date and start_date > end_date:
        st.error("A data inicial não pode ser posterior à data final.")
    else:
        current_stats = stats(conn)
        model = None
        try:
            if current_stats["segments"] > current_stats["pending"]:
                model = load_model()
        except Exception as exc:
            st.warning(f"Modelo semântico indisponível ({exc}). Será utilizada somente a busca textual.")
        if current_stats["pending"]:
            st.warning("Há trechos ainda sem índice semântico. Gere/atualize o índice na lateral para obter a busca inteligente completa.")
        if model is None:
            st.warning("Busca lexical de contingência: para pesquisar por significado, instale e execute a indexação por IA.")
        filters = {
            "area":area, "orgao":orgao, "relator":relator, "classe":classe, "tribunal":tribunal_filter,
            "start_date":start_date.isoformat() if start_date else "",
            "end_date":end_date.isoformat() if end_date else "",
            "numero": numero,
        }
        try:
            with st.spinner("Pesquisando o acervo local..."):
                results = search(conn, query, filters, model=model, exclude=exclude,
                                 limit=30, min_similarity=min_similarity)
            st.write(f"**{len(results)} resultado(s) exibido(s)** — {'busca semântica e textual' if model is not None else 'busca textual'}")
            if not results:
                st.info("Sem correspondências no acervo atual. Ajuste os filtros ou importe mais edições.")
            for i, hit in enumerate(results,1):
                with st.container(border=True):
                    st.markdown(f"**{i}. {hit['title']}**")
                    st.caption(f"Tribunal: **{hit['tribunal']}** · Processo: {hit['numero']} · {hit['area']} · {hit['classe'] or 'Classe não informada'} · Julgado em {safe_date(hit['julgamento'])}")
                    st.caption(f"Relator(a): {hit['relator'] or 'Não informado'} · {hit['orgao'] or 'Órgão não informado'}")
                    excerpt = hit["excerpt"] or hit["ementa"]
                    st.write((excerpt[:950] + "…") if len(excerpt)>950 else excerpt)
                    c1,c2,c3 = st.columns([1.4,1.4,2])
                    source = hit["source_url"]
                    if source and source_link_allowed(source, hit["tribunal"]):
                        if hit["tribunal"] == "TJMT":
                            c1.link_button("📄 URL oficial informada", f"{source}#page={hit['pagina_inicio']}")
                        else:
                            c1.link_button("🔗 URL oficial informada", source)
                    path = UPLOADS/(hit["file_sha256"]+".pdf")
                    if path.is_file():
                        c2.download_button("Baixar PDF importado", data=path.read_bytes(),
                                           file_name=f"jurismt_{hit['tribunal'].lower().replace('-', '')}_{hit['id']}.pdf",
                                           mime="application/pdf", key=f"download_{hit['id']}")
                    c3.caption(f"{hit['edicao']} · págs. {hit['pagina_inicio']}–{hit['pagina_fim']} · índice {hit['rank_score']:.3f}")
                    with st.expander("Ver texto extraído"):
                        st.text(hit["ementa"])
                        st.caption("Confira o documento oficial antes de citar. A extração pode conter problemas de formatação.")
                    with st.expander("Corrigir metadados (uso local)"):
                        st.caption("A edição não modifica o PDF, a ementa original nem o número do processo. URLs declaradas não são comprovadas automaticamente.")
                        with st.form(key=f"edit_{hit['id']}"):
                            edit_title = st.text_input("Título", value=hit["title"])
                            edit_relator = st.text_input("Relator(a)", value=hit["relator"] or "")
                            edit_orgao = st.text_input("Órgão julgador", value=hit["orgao"] or "")
                            edit_classe = st.text_input("Classe", value=hit["classe"] or "")
                            edit_date = st.text_input("Data do julgamento (AAAA-MM-DD ou DD/MM/AAAA)", value=hit["julgamento"] or "")
                            edit_pub = st.text_input("Data da publicação (AAAA-MM-DD ou DD/MM/AAAA)", value=hit["publicacao"] or "")
                            edit_url = st.text_input("URL de origem declarada", value=hit["source_url"] or "")
                            if st.form_submit_button("Salvar correção"):
                                try:
                                    update_document_metadata(conn, hit["id"], {
                                        "title":edit_title, "relator":edit_relator, "orgao":edit_orgao,
                                        "classe":edit_classe, "julgamento":edit_date,
                                        "publicacao":edit_pub, "source_url":edit_url,
                                    })
                                    st.success("Metadados atualizados. O documento original foi preservado.")
                                    st.rerun()
                                except ValueError as exc:
                                    st.error(str(exc))
        except Exception as exc:
            st.error(f"Falha na busca: {exc}")

st.divider()
st.caption("Protótipo independente, sem vínculo com TJMT, TRT-23 ou CSJT. Não utiliza APIs privadas. Os metadados trabalhistas são informados pelo operador e devem ser verificados. Utilize somente documentos públicos e observe a LGPD.")
conn.close()
