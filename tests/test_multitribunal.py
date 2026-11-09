"""Testes sintéticos: não constituem decisões reais, nem são importados no acervo do usuário."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
import engine


def synthetic_trt_pdf():
    pdf = fitz.open()
    page = pdf.new_page(width=620, height=830)
    page.insert_text((42, 70),
        "TRT DA 23A REGIAO - DOCUMENTO TOTALMENTE SINTETICO\n"
        "Processo 0000123-57.2025.5.23.0001\n"
        "TESTE DE HORAS EXTRAS E JORNADA.\n"
        "Este e um texto ficticio de treinamento tecnico. "
        "Nao representa decisao, entendimento ou precedente judicial.\n"
        "O objetivo e verificar a importacao em separado de tribunais.\n", fontsize=10)
    content = pdf.tobytes()
    pdf.close()
    return content


class MultiTribunalTests(unittest.TestCase):
    def test_url_isolation(self):
        self.assertTrue(engine.source_link_allowed('https://pje.trt23.jus.br/jurisprudencia/', 'TRT-23'))
        self.assertTrue(engine.source_link_allowed('https://jurisprudencia.jt.jus.br/jurisprudencia-nacional/home', 'TRT-23'))
        self.assertFalse(engine.source_link_allowed(engine.OFFICIAL_EDITION_35, 'TRT-23'))
        self.assertFalse(engine.source_link_allowed('https://pje.trt23.jus.br/jurisprudencia/', 'TJMT'))
        self.assertFalse(engine.source_link_allowed('https://pje.trt23.jus.br.evil.com/', 'TRT-23'))

    def test_extract_import_search(self):
        with tempfile.TemporaryDirectory() as temp:
            conn = engine.connect(Path(temp) / 'multitribunal.sqlite')
            upload_before = engine.UPLOADS
            engine.UPLOADS = Path(temp) / 'uploads'
            try:
                pdf = synthetic_trt_pdf()
                meta = {'classe': 'ROT', 'relator': 'RELATOR DE TESTES', 'orgao': 'TURMA DE TESTES',
                        'julgamento': '03/11/2026'}
                result = engine.import_trt23_pdf(conn, pdf, 'https://pje.trt23.jus.br/jurisprudencia/', meta)
                self.assertEqual(result['inserted'], 1)
                self.assertEqual(engine.import_trt23_pdf(conn, pdf, metadata=meta)['duplicates'], 1)
                self.assertEqual(engine.stats(conn)['trt23'], 1)
                self.assertEqual(engine.stats(conn)['tjmt'], 0)
                results = engine.search(conn, 'horas extras', {'tribunal': 'TRT-23'})
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]['tribunal'], 'TRT-23')
                self.assertEqual(results[0]['classe'], 'ROT')
                self.assertEqual(results[0]['julgamento'], '2026-11-03')
                self.assertEqual(engine.search(conn, 'horas extras', {'tribunal': 'TJMT'}), [])
            finally:
                conn.close()
                engine.UPLOADS = upload_before

    def test_combined_search_by_tribunal(self):
        from test_pipeline import fake_pdf
        with tempfile.TemporaryDirectory() as temp:
            conn = engine.connect(Path(temp) / 'combined.sqlite')
            old_uploads = engine.UPLOADS
            engine.UPLOADS = Path(temp) / 'uploads'
            try:
                engine.import_pdf(conn, fake_pdf(), 'TESTE')
                engine.import_trt23_pdf(conn, synthetic_trt_pdf())
                self.assertEqual(engine.stats(conn)['documents'], 3)
                self.assertEqual(len(engine.search(conn, 'teste', {'tribunal': 'TJMT'})), 2)
                self.assertEqual(len(engine.search(conn, 'teste', {'tribunal': 'TRT-23'})), 1)
                both = engine.search(conn, 'teste', {'tribunal': 'Todas'})
                self.assertEqual(len(both), 3)
            finally:
                conn.close()
                engine.UPLOADS = old_uploads

    def test_migration_v1_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'old.sqlite'
            c = sqlite3.connect(path)
            c.execute("""CREATE TABLE documents (
                id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                ementa TEXT NOT NULL, numero TEXT NOT NULL, classe TEXT, relator TEXT,
                orgao TEXT, area TEXT, julgamento TEXT, publicacao TEXT, edicao TEXT,
                source_url TEXT, file_sha256 TEXT, pagina_inicio INTEGER,
                pagina_fim INTEGER, imported_at TEXT NOT NULL)""")
            c.execute("INSERT INTO documents(sha256,title,ementa,numero,imported_at) "
                      "VALUES ('hash', 'anterior', 'texto', '1001234-09.2026.8.11.0001', '2026-01-01')")
            c.commit()
            c.close()
            # simula o caso mínimo de uma instalação antiga com tabela preexistente.
            # Esquema real 1.0 contém as demais colunas e migra usando ALTER.
            c2 = engine.connect(path)
            self.assertEqual(c2.execute('SELECT tribunal FROM documents WHERE id=1').fetchone()[0], 'TJMT')
            c2.close()

    def test_ambiguous_process_requires_manual_confirmation(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((40, 50), 'Documento TRT23 com ementa sintetica de teste e descricao extensa.\n'
                         'Referencias: 0000123-57.2025.5.23.0001 e 0004567-96.2026.5.23.0002.\n'
                         'Texto para verificar extracao segura sem assumir qual o processo principal.\n', fontsize=10)
        blob = pdf.tobytes()
        pdf.close()
        with self.assertRaises(ValueError):
            engine.extract_trt23_document(blob)
        info = engine.extract_trt23_document(blob, {'numero':'0000123-57.2025.5.23.0001'})
        self.assertEqual(info['numero'], '0000123-57.2025.5.23.0001')

if __name__ == '__main__':
    unittest.main()
