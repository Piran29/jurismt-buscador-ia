"""Regressões sintéticas de confiabilidade; não contêm decisões reais."""
from pathlib import Path
import sys
import tempfile
import unittest
import fitz
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


class FakeModel:
    def encode(self, texts, **kwargs):
        return np.array([[1.0, 0.0] for _ in texts], dtype=np.float32)


def add_doc(conn, title, text, orgao="Órgão", vector=None):
    cur = conn.execute("""INSERT INTO documents
        (sha256,title,ementa,numero,orgao,area,imported_at)
        VALUES (?,?,?,?,?,?,?)""",
        (f"hash_{title}", title, text, "1001234-09.2026.8.11.0001",
         orgao, "Direito Privado", "2026-11-09"))
    conn.execute("""INSERT INTO segments(document_id,part_idx,content,embedding,embedding_model)
                   VALUES(?,0,?,?,?)""", (cur.lastrowid, text,
        np.asarray(vector, dtype=np.float32).tobytes() if vector is not None else None,
        engine.MODEL_NAME if vector is not None else None))
    conn.commit()
    return cur.lastrowid


def trt_pdf(text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((30, 60), text, fontsize=9)
    blob = doc.tobytes()
    doc.close()
    return blob


class ReliabilityTests(unittest.TestCase):
    def test_cnj_dv_and_tribunal(self):
        self.assertTrue(engine.valid_cnj("0000123-57.2025.5.23.0001", "TRT-23"))
        self.assertTrue(engine.valid_cnj("1001234-09.2026.8.11.0001", "TJMT"))
        self.assertFalse(engine.valid_cnj("0000123-45.2025.5.23.0001", "TRT-23"))
        self.assertFalse(engine.valid_cnj("0000123-57.2025.5.23.0001", "TJMT"))

    def test_pdf_cnj_disagreement_requires_confirmation(self):
        pdf = trt_pdf("TRT SINTETICO 0000123-57.2025.5.23.0001\n" +
                      "Teste de numero processual e referencia ao texto. " * 5)
        with self.assertRaisesRegex(ValueError, "não consta"):
            engine.extract_trt23_document(pdf, {"numero": "0004567-96.2026.5.23.0002"})
        doc = engine.extract_trt23_document(pdf, {
            "numero": "0004567-96.2026.5.23.0002",
            "confirmar_numero_divergente": True})
        self.assertEqual(doc["numero"], "0004567-96.2026.5.23.0002")
        with self.assertRaisesRegex(ValueError, "dígito verificador"):
            engine.extract_trt23_document(pdf, {"numero": "0000123-45.2025.5.23.0001"})

    def test_low_semantic_similarity_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = engine.connect(Path(tmp) / "test.sqlite")
            add_doc(conn, "Um", "tema completamente diferente", vector=[0.0, 1.0])
            self.assertEqual(engine.search(conn, "termos sem correspondencia", {}, model=FakeModel()), [])
            add_doc(conn, "Dois", "assunto sem palavras da consulta", vector=[0.8, 0.6])
            self.assertEqual(len(engine.search(conn, "termos sem correspondencia", {}, model=FakeModel())), 1)
            self.assertEqual(len(engine.search(conn, "termos sem correspondencia", {}, model=FakeModel(),
                                                min_similarity=0.9)), 0)
            conn.close()

    def test_edit_metadata_and_fts(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = engine.connect(Path(tmp) / "test.sqlite")
            doc_id = add_doc(conn, "Antes", "Texto de testes")
            engine.update_document_metadata(conn, doc_id, {
                "title": "Atualizado", "julgamento": "09/11/2026",
                "source_url": engine.OFFICIAL_EDITION_35, "orgao": "Câmara de teste"})
            row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            self.assertEqual(row["julgamento"], "2026-11-09")
            self.assertEqual(len(engine.search(conn, "Atualizado", {})), 1)
            with self.assertRaisesRegex(ValueError, "URL"):
                engine.update_document_metadata(conn, doc_id, {"source_url": "https://exemplo.com"})
            with self.assertRaisesRegex(ValueError, "editáveis"):
                engine.update_document_metadata(conn, doc_id, {"numero": "trocar"})
            conn.close()

    def test_filter_before_lexical_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = engine.connect(Path(tmp) / "test.sqlite")
            for i in range(2100):
                conn.execute("""INSERT INTO documents
                    (sha256,title,ementa,numero,orgao,area,imported_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (f"dummy_{i}", f"Termo {i}", "teste comum",
                     "1001234-09.2026.8.11.0001", "outro órgão",
                     "Direito Privado", "2026-11-09"))
            conn.commit()
            add_doc(conn, "Alvo", "teste termo do documento", orgao="órgão alvo")
            results = engine.search(conn, "teste", {"orgao": "órgão alvo"})
            self.assertEqual([r["title"] for r in results], ["Alvo"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
