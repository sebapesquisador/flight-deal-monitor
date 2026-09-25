@echo off
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Monitor de Passagens
cd /d "%~dp0"

echo.
echo  ============================================================
echo    MONITOR DE PASSAGENS - precos reais do Google Flights
echo  ============================================================
echo.

REM ---- 1) achar o Python -------------------------------------
where python >nul 2>&1
if not errorlevel 1 set PY=python& goto ACHOU
where py >nul 2>&1
if not errorlevel 1 set PY=py -3& goto ACHOU
goto SEMPYTHON

:ACHOU
REM ---- 2) testar o conector ----------------------------------
echo  [1/3] Testando o conector de precos...
%PY% verificar.py >nul 2>&1
if not errorlevel 1 goto RODAR

REM ---- 3) consertar sozinho ----------------------------------
echo        Faltam pecas. Vou instalar automaticamente.
echo.
echo  [2/3] Reparando as dependencias (pode levar 1 minuto)...
%PY% consertar.py
echo.

REM ---- 4) testar de novo -------------------------------------
echo  [3/3] Testando outra vez...
%PY% verificar.py
if errorlevel 1 goto NAODEU

:RODAR
echo.
echo  ============================================================
echo    Tudo certo. Abrindo o painel no navegador.
echo.
echo    Se aparecer uma faixa VERMELHA escrito
echo    "DADOS SIMULADOS", algo falhou: copie o texto desta
echo    janela e me envie.
echo.
echo    DEIXE ESTA JANELA ABERTA.
echo    Para parar: Ctrl+C  ou  feche a janela.
echo  ============================================================
echo.

%PY% servidor.py 8000
goto FIM

:NAODEU
echo.
echo  ============================================================
echo    NAO CONSEGUI RESOLVER SOZINHO.
echo.
echo    Copie TODO o texto desta janela e me envie.
echo    Com o erro exato eu resolvo na hora.
echo.
echo    Para copiar: clique com o botao direito na barra de
echo    titulo ^> Editar ^> Selecionar tudo ^> Enter
echo  ============================================================
echo.
pause
goto FIM

:SEMPYTHON
echo  [ERRO] Nao achei o Python neste computador.
echo.
echo  Como resolver:
echo   1. Abra  https://www.python.org/downloads/
echo   2. Clique no botao amarelo de download
echo   3. Ao instalar, MARQUE a caixa "Add Python to PATH"
echo   4. Feche e abra este arquivo INICIAR.bat de novo
echo.
pause
goto FIM

:FIM
