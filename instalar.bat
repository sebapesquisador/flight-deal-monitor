@echo off
setlocal EnableDelayedExpansion

REM =====================================================================
REM  Flight Deal Monitor - instalacao automatica (CMD / Prompt de Comando)
REM  Uso:  instalar.bat
REM        (ou clique duas vezes no arquivo)
REM =====================================================================

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

echo ============================================================
echo  Flight Deal Monitor  ^-  instalacao (Windows)
echo ============================================================
echo  Diretorio: %CD%
echo.

REM ---------- 1) localizar o Python ----------
set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"

if not defined PYCMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)

if not defined PYCMD (
    echo [ERRO] Python nao encontrado no PATH.
    echo.
    echo 1. Instale em https://www.python.org/downloads/
    echo 2. IMPORTANTE: marque "Add python.exe to PATH"
    echo 3. Feche e reabra este terminal, e rode instalar.bat novamente.
    echo.
    pause
    exit /b 1
)

echo [ok] Interpretador: %PYCMD%
%PYCMD% --version
echo.

REM ---------- 2) ambiente virtual ----------
if exist ".venv\Scripts\python.exe" (
    echo [ok] Ambiente virtual .venv ja existe.
) else (
    echo [..] Criando ambiente virtual em .venv ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o venv.
        pause
        exit /b 1
    )
    echo [ok] Ambiente virtual criado.
)

set "VPY=%CD%\.venv\Scripts\python.exe"
echo.

REM ---------- 3) dependencias ----------
echo [..] Instalando dependencias ^(pode levar 1-2 minutos^) ...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao instalar as dependencias.
    echo Dica: se voce esta atras de proxy/VPN corporativo, tente:
    echo   "%VPY%" -m pip install -r requirements.txt --proxy http://usuario:senha@proxy:8080
    pause
    exit /b 1
)
echo [ok] Dependencias instaladas.
echo.

REM ---------- 4) banco de dados ----------
echo [..] Criando o schema do banco ^(SQLite em .\data\monitor.db^) ...
"%VPY%" -m src.cli init-db
if errorlevel 1 (
    echo [ERRO] Falha ao criar o schema.
    pause
    exit /b 1
)

echo [..] Criando um monitoramento de exemplo ^(GRU-LAX, 50%% do mercado^) ...
"%VPY%" -m src.cli seed
echo.

REM ---------- 5) demonstracao ----------
echo ============================================================
echo  Demonstracao end-to-end ^(fontes sinteticas^)
echo ============================================================
"%VPY%" -m src.cli demo
echo.

echo ============================================================
echo  PRONTO. Comandos do dia a dia ^(dentro desta pasta^):
echo.
echo    .venv\Scripts\python -m src.cli api      painel em http://localhost:8000
echo    .venv\Scripts\python -m src.cli scan     roda um ciclo de monitoramento
echo    .venv\Scripts\python -m src.cli demo     repete a demonstracao
echo    .venv\Scripts\python -m pytest           roda os 44 testes
echo.
echo  Atalho: use "python -m src.cli ..." sem o .venv se preferir
echo  instalar globalmente ^(nao recomendado^).
echo ============================================================
pause
endlocal
