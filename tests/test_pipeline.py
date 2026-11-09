"""Usa PDF inteiramente sintético: NENHUM acórdão inventado vira dado de usuário."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
import engine


def fake_pdf() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page(width=600,height=820)
    story = (
        "Direito Privado\n"
        "DIREITO CONSUMIDOR - TESTE SINTETICO DE TITULO.\n"
        "APELACAO CIVEL. DESCRICAO TOTALMENTE FICTICIA PARA TESTAR O PARSER.\n"
        "O sistema deve encontrar este texto longo o bastante para a validacao do teste.\n"
        "Este documento nao e jurisprudencia real e jamais sera usado no portal.\n"
        "(TJMT, APELACAO CIVEL 1001234-56.2026.8.11.0001, Relator(a) PESSOA DE TESTE,\n"
        "Primeira Camara de Testes, Julgado em 12/09/2026, Publicado em 15/09/2026)\n"
        "DIREITO PROCESSUAL CIVIL - SEGUNDO JULGADO DE TESTE.\n"
        "APELACAO CIVEL. SEGUNDO TEXTO SINTETICO PARA FINS DE TESTE.\n"
        "Esta ementa demonstrativa nao e decisao judicial nem deve ser citada.\n"
        "Utilizada apenas para garantir funcionamento do parser e dos filtros.\n"
        "(TJMT, AGRAVO DE INSTRUMENTO 1009876-54.2026.8.11.0002, Relator(a) OUTRA PESSOA TESTE,\n"
        "Segunda Camara de Testes, Julgado em 18/09/2026, Publicado em 20/09/2026)\n"
    )
    page.insert_text((45,40), story,fontsize=10, lineheight=1.3)
    out=pdf.tobytes()
    pdf.close()
    return out


class PipelineTests(unittest.TestCase):
    def test_source_hosts(self):
        self.assertTrue(engine.source_link_allowed(engine.OFFICIAL_EDITION_35))
        self.assertFalse(engine.source_link_allowed("https://jurisprudencia.tjmt.jus.br.bad.com/dado"))
        self.assertFalse(engine.source_link_allowed("http://jurisprudencia.tjmt.jus.br/dado"))

    def test_extract_and_search(self):
        content = fake_pdf()
        docs = engine.extract_ementario(content, "TESTE")
        self.assertEqual(len(docs),2,docs)
        self.assertEqual(docs[0]['numero'],'1001234-56.2026.8.11.0001')
        self.assertEqual(docs[0]['area'],'Direito Privado')
        self.assertEqual(docs[1]['julgamento'],'2026-09-18')
        with tempfile.TemporaryDirectory() as temp:
            conn = engine.connect(Path(temp)/'test.sqlite')
            old=engine.UPLOADS
            engine.UPLOADS=Path(temp)/'uploads'
            try:
                result=engine.import_pdf(conn, content,"TESTE",engine.OFFICIAL_EDITION_35)
                self.assertEqual(result['inserted'],2)
                again=engine.import_pdf(conn, content,"TESTE",engine.OFFICIAL_EDITION_35)
                self.assertEqual(again['duplicates'],2)
                matches=engine.search(conn,"sintetico",{},limit=3)
                self.assertEqual(len(matches),2)
                filtered=engine.search(conn,"sintetico",{"relator":"OUTRA PESSOA TESTE"})
                self.assertEqual(len(filtered),1)
                self.assertEqual(filtered[0]['numero'],'1009876-54.2026.8.11.0002')
                self.assertEqual(engine.stats(conn)['documents'],2)
            finally:
                conn.close()
                engine.UPLOADS=old

    def test_chunks_overlap(self):
        chunks=engine.chunks_of(" ".join(str(i) for i in range(350)))
        self.assertGreater(len(chunks),2)
        self.assertIn("150",chunks[0])
        self.assertIn("150",chunks[1])

if __name__=='__main__':
    unittest.main()
