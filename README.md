# JurisMT 1.1 — buscador privado de jurisprudências TJMT + TRT-23

**Estado: MVP multitribunal local (experimental).** Não é serviço oficial de nenhum tribunal, não tem cobertura integral dos acervos, e toda informação deve ser conferida na decisão original.

## Atualização da versão 1.0 para a 1.1 (sem perder dados)

1. Feche a janela do JurisMT e quaisquer processos do Streamlit que estejam utilizando o banco.
2. Faça uma cópia de segurança de **`jurismt_mvp/data/`**, caso já tenha importado jurisprudências. Opcionalmente, copie toda a pasta do projeto.
3. Abra o novo ZIP e copie **`app.py`, `engine.py`, `iniciar_windows.bat`, `README.md` e os arquivos de testes** para a sua pasta `jurismt_mvp` anterior, substituindo os arquivos de código. **Não apague nem substitua `data/` nem `.venv/`.**
4. Execute `iniciar_windows.bat`. O código aplicará automaticamente uma alteração **não destrutiva** no banco SQLite já existente: adiciona o campo `tribunal`, identificando as decisões antigas como `TJMT`.
5. Reutilize os vetores de IA das ementas antigas. Só os documentos trabalhistas recém-importados precisam de indexação adicional.

Se a pasta `.venv` estiver configurada com outro Python, o novo inicializador mostrará instruções para renomeá-la. **Nunca renomeie ou exclua a pasta `data/` nessa operação.**

## Importar decisões do TRT-23

1. Abra a interface de pesquisa oficial do [TRT-23](https://pje.trt23.jus.br/jurisprudencia/) ou o [Sistema Falcão](https://jurisprudencia.jt.jus.br/jurisprudencia-nacional/home), selecionando TRT-23 na pesquisa do Falcão.
2. Consulte e **baixe manualmente** um PDF de decisão/acórdão **público**, sem sigilo. Não há raspagem automatizada dessas plataformas.
3. Na barra lateral do JurisMT, selecione **TRT-23 — Acórdão ou decisão individual** e envie o PDF (um julgamento por arquivo).
4. Preencha o número do processo CNJ, se possível. Se houver mais de um número no PDF, o sistema exige confirmação manual para não confundir precedentes citados com o processo principal. A classe, relator, órgão e datas são opcionais, mas melhoram a precisão dos filtros.
5. Opcionalmente, inclua a **URL específica da fonte oficial**; uma URL genérica do portal apenas abre a pesquisa, não comprova diretamente o julgado.
6. Clique em **Gerar / atualizar índice de IA** e use o filtro **Tribunal: TRT-23, TJMT ou Todas**.

**Limites atuais da extração TRT-23:** um PDF = um documento. O importador lê texto extraível, não faz OCR, não separa automaticamente PDFs com múltiplos julgados e **não deduz com segurança relator, classe ou datas**. Confirme os dados manualmente; PDFs sem texto ou sem número CNJ identificável precisam de tratamento adicional. O conteúdo extraído pode ser o inteiro teor, diferentemente das ementas do TJMT.



## Como instalar (Windows)

1. Instale **Python 3.11 (64 bits)**, com a opção *Add Python to PATH* (ou com o launcher `py`).
2. Extraia o arquivo ZIP em uma pasta do seu computador.
3. Execute `iniciar_windows.bat` (ou os comandos abaixo em um terminal). A primeira instalação requer internet.
4. O navegador deverá abrir em `http://127.0.0.1:8501`.
5. Na barra lateral, clique em **Importar PDF oficial da edição 35**. Se houver falha na conexão do TJMT, abra o link para o **35º Ementário** e faça o download pelo navegador; depois envie o PDF pelo campo de upload.
6. Clique em **Gerar / atualizar índice de IA**. O modelo gratuito será baixado uma vez para o computador (pode ser relativamente grande).
7. Pesquise descrevendo os fatos ou a tese jurídica em linguagem natural. Ajuste os filtros e abra o PDF original para confirmar o julgamento.

### Instalação por terminal

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 127.0.0.1
```

Se a política do PowerShell impedir `Activate.ps1`, use o Prompt de Comando (`cmd.exe`) e `.venv\Scripts\activate.bat`, ou rode diretamente `.venv\Scripts\python.exe -m streamlit run app.py`.

### Funcionamento com instalação leve (sem modelo)

`python -m pip install -r requirements-lite.txt` permite **apenas busca textual**. Para IA semântica é obrigatório o modelo da dependência `sentence-transformers` e gerar o índice. O funcionamento local não exige uma chave de API paga.

## Fonte de documentos

- **35º Ementário Eletrônico (outubro de 2026)**, link oficial divulgado em 4/11/2026:
  https://www.tjmt.jus.br/noticias/2026/11/nova-edicao-ementario-eletronico-reune-entendimentos-camaras-julgadoras-tjmt
- **PDF público e original:**
  https://cms-midia.tjmt.jus.br/dsaportaltjmt/biblioteca-midia/108b0000-0aa5-0a58-2eca-08df3a7fe147/108b0000-0aa5-0a58-2fd3-08df3a7fe147.pdf
- **Consulta do Ementário**: https://jurisprudencia.tjmt.jus.br/ementario/consulta?edicao=35&ano=2026

**Atenção:** o ementário é uma **seleção** de julgados e o PDF contém principalmente ementas, não o inteiro teor de todos os acórdãos. A precisão da importação e a amplitude da amostra precisam ser validadas por um profissional do Direito.

## Arquitetura

```text
PDF público: Ementário TJMT ou documento individual TRT-23
  ↓  parser TJMT (ementas) ou leitor TRT-23 (texto integral + metadados manuais)
SQLite local (documentos separados por tribunal)
  ├── documents + FTS5 → busca textual com palavras, operadores e acentos
  └── segments (trechos de ~155 palavras) + embeddings → busca semântica
                                                          ↓
Modelo local multilíngue MiniLM (CPU, sem API externa para consultas)
  ↓
Busca híbrida: ranking semântico e BM25 + filtros SQL
  ↓
Interface Streamlit → trechos literais + referência ao PDF para conferência
```

O modelo de *embedding* é `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. O rank é uma métrica comparativa interna, **não** probabilidade de procedência ou validade jurídica.

## Segurança, permissões e privacidade

- **O único download programático é limitado a UM PDF público e conhecido do TJMT** e depende de um clique do operador. Não existem robôs de navegação, scraping em massa, contorno de CAPTCHA, autenticação indevida ou uso de endpoints privados. Se o TJMT restringir downloads automatizados, use **download manual e upload**, ou obtenha uma permissão expressa do tribunal.
- A verificação das condições de uso/robots aplicáveis ao acervo e a autorização para coleta em escala devem ser feitas antes de implementar sincronização ou varredura de novas edições. **Acesso público não é autorização irrestrita de raspagem em escala.**
- A API Pública do DataJud/CNJ fornece metadados processuais, **não uma base completa de ementas ou inteiros teores**: não é usada neste protótipo.
- Somente PDFs públicos. Não importe arquivos sigilosos nem informações pessoais desnecessárias. O banco e os PDFs importados ficam na pasta `data/`; proteja-a com permissões do Windows e backup criptografado, se necessário.
- Os acórdãos TRT-23 só são importados por **upload manual**; não há acesso à API interna do PJe nem ao Falcão.
- O portal **não tem login e não deve ser exposto à rede ou à internet**. Ele atende no endereço local `127.0.0.1`. Para acesso remoto no futuro, exija autenticação e HTTPS por proxy/VPN.
- Após o download inicial do modelo, as pesquisas e a geração de embeddings são locais (sem encaminhar perguntas a uma API de IA). A telemetria de uso do Streamlit está desabilitada no arquivo `.streamlit/config.toml`.
- **Sem OCR.** PDFs digitalizados como imagem ou modelos de ementários diferentes podem não ser reconhecidos. O importador foi projetado para o formato editorial observado na edição 35, mas não foi validado diretamente com os bytes do PDF oficial na elaboração deste pacote.

## Limitações atuais

1. O parser do TJMT foi implementado conforme o padrão textual da **edição 35** do Ementário. Ele pode deixar alguns julgados de fora, concatenar cabeçalhos e captar formatação imperfeita; confira o PDF.
2. Busca semântica encontra similaridade, **não verifica precedente obrigatório, vigência, superação, posição majoritária, divergência ou relevância jurídica**.
3. Pesquisa nas **ementas TJMT** e nos **PDFs individuais TRT-23** importados, não no inteiro teor de decisões que não constem do acervo.
4. Não há sincronização contínua nem promessas de cobertura exaustiva.
5. Não há geração de respostas jurídicas por LLM; apenas recuperação de trechos literais com fonte.
6. Não há controle de usuários; somente uso em uma máquina local.

## Evoluções planejadas

- Revisão manual de metadados e contagem de julgados por edição; mais edições públicas.
- Opção de importar JSON/CSV de fonte oficialmente autorizada, mais indexação de inteiro teor quando disponível.
- Re-ranker jurídico ou avaliação do modelo em consultas reais; métricas Recall@10, Precision@10 e nDCG.
- Permissões de usuário, trilhas de auditoria, backups e eventualmente serviço web privado.
- Respostas em linguagem natural baseadas em documentos recuperados (*RAG*), com citações, sempre sujeitas a validação humana.

## Testes

Rode `python -m pytest -q tests` (requer `pytest`) para testar o parser com PDF **sintético claramente identificado como teste** e a busca textual. Não existem decisões fictícias embarcadas na interface nem jurisprudências fabricadas como amostras de uso.


## Confiabilidade — melhorias propostas na v1.2

- **Validação CNJ:** números de processo passam por validação de estrutura e dígito verificador (módulo 97). Isso não comprova que o processo existe. Divergências entre o número informado e números válidos extraídos do PDF TRT-23 exigem confirmação explícita.
- **Busca híbrida:** filtro experimental de similaridade semântica com valor inicial 0,40, configurável na tela. O valor não é probabilidade jurídica e exige calibração com pesquisas reais. Correspondências textuais podem continuar aparecendo abaixo desse limiar.
- **Filtros e desempenho:** filtros estruturados passam a ser aplicados na consulta ao índice textual e aos vetores antes da ordenação; acervos maiores ainda precisarão de medições e possivelmente um índice vetorial dedicado.
- **Metadados:** formulário local para corrigir título, classe, relator, órgão, datas e URL declarada, preservando texto, número e hash originais. Atualizações de conteúdo e remoções não foram implementadas.
- **Proveniência:** URLs seguem restritas a domínios oficiais, mas sua correspondência ao acórdão não é verificada automaticamente. Confira sempre a decisão original.
- **Qualidade:** novos testes sintéticos e workflow GitHub Actions para Pull Requests. Os testes não carregam o modelo de IA completo nem confirmam a extração real dos acervos.

Execute `python -m unittest discover -s tests -v` depois de instalar `requirements-lite.txt` para rodar os testes locais.
