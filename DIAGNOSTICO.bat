@echo off
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Diagnostico - Monitor de Passagens
cd /d "%~dp0"

set "REL=diagnostico.txt"

echo ================================================== > %REL%
echo  DIAGNOSTICO - MONITOR DE PASSAGENS               >> %REL%
echo  %date%  %time%                                   >> %REL%
echo ================================================== >> %REL%

echo. >> %REL%
echo [0] PROCURANDO O PYTHON >> %REL%
echo. >> %REL%

set "PYX="
where python >nul 2>&1
if not errorlevel 1 set "PYX=python"
if not defined PYX (
  where py >nul 2>&1
  if not errorlevel 1 set "PYX=py"
)

if not defined PYX (
  echo  RESULTADO: PYTHON NAO ENCONTRADO >> %REL%
  echo. >> %REL%
  echo  O Python nao esta instalado ou nao esta no PATH. >> %REL%
  echo  Instale em https://www.python.org/downloads/ >> %REL%
  echo  e MARQUE a opcao "Add Python to PATH". >> %REL%
  goto MOSTRAR
)

echo  Comando python encontrado: %PYX% >> %REL%
%PYX% --version >> %REL% 2>&1
echo. >> %REL%

echo ================================================== >> %REL%
echo [A] VERIFICACAO DOS MODULOS >> %REL%
%PYX% verificar.py >> %REL% 2>&1
echo. >> %REL%
echo ================================================== >> %REL%
%PYX% diagnostico.py >> %REL% 2>&1
echo ================================================== >> %REL%

:MOSTRAR
echo.
type %REL%
echo.
echo --------------------------------------------------
echo  O arquivo  diagnostico.txt  foi criado
echo  nesta mesma pasta.
echo.
echo  Me envie esse arquivo (ele esta em:
echo  %CD% )
echo --------------------------------------------------
echo.
pause
