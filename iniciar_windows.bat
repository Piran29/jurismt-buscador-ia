@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher nao encontrado. Instale Python 3.11 para Windows.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente Python 3.11...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo Nao foi possivel criar o ambiente. Confira: py -3.11 --version
        pause
        exit /b 1
    )
)
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)"
if errorlevel 1 (
    echo [ERRO] O ambiente .venv nao usa Python 3.11.
    echo Feche este programa, renomeie a pasta .venv para .venv_antiga
    echo e execute este arquivo novamente para criar um novo ambiente.
    echo NAO apague a pasta data, que contem suas jurisprudencias.
    pause
    exit /b 1
)
echo Instalando ou verificando dependencias...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias. Confira sua conexao de internet.
    pause
    exit /b 1
)
echo Abrindo JurisMT em http://127.0.0.1:8501
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
pause
