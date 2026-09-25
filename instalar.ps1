# =====================================================================
#  Flight Deal Monitor - instalacao automatica (PowerShell)
#  Uso:  .\instalar.ps1
#        Se der "execucao de script desabilitada", rode:
#        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# =====================================================================
$ErrorActionPreference = "Stop"

chcp 65001 | Out-Null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location -Path $PSScriptRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Flight Deal Monitor  -  instalacao (Windows/PowerShell)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Diretorio: $(Get-Location)"
Write-Host ""

# ---------- 1) localizar o Python ----------
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue)      { $py = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python"; $pyArgs = @() }

if (-not $py) {
    Write-Host "[ERRO] Python nao encontrado no PATH." -ForegroundColor Red
    Write-Host "Instale em https://www.python.org/downloads/ marcando 'Add python.exe to PATH'." -ForegroundColor Yellow
    exit 1
}
Write-Host "[ok] Interpretador: $py $($pyArgs -join ' ')" -ForegroundColor Green
& $py @pyArgs --version

# ---------- 2) ambiente virtual ----------
if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "[ok] Ambiente virtual .venv ja existe." -ForegroundColor Green
} else {
    Write-Host "[..] Criando ambiente virtual em .venv ..." -ForegroundColor Yellow
    & $py @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o venv." }
    Write-Host "[ok] Ambiente virtual criado." -ForegroundColor Green
}
$vpy = Join-Path (Get-Location) ".venv\Scripts\python.exe"

# ---------- 3) dependencias ----------
Write-Host "[..] Instalando dependencias (pode levar 1-2 minutos) ..." -ForegroundColor Yellow
& $vpy -m pip install --upgrade pip --disable-pip-version-check
& $vpy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERRO] Falha ao instalar dependencias." -ForegroundColor Red
    Write-Host "Dica (proxy corporativo):" -ForegroundColor Yellow
    Write-Host "  & '$vpy' -m pip install -r requirements.txt --proxy http://usuario:senha@proxy:8080"
    exit 1
}
Write-Host "[ok] Dependencias instaladas." -ForegroundColor Green

# ---------- 4) banco + exemplo ----------
Write-Host "[..] Criando o schema do banco (SQLite em .\data\monitor.db) ..." -ForegroundColor Yellow
& $vpy -m src.cli init-db
Write-Host "[..] Criando monitoramento de exemplo (GRU-LAX, 50% do mercado) ..." -ForegroundColor Yellow
& $vpy -m src.cli seed

# ---------- 5) demonstracao ----------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Demonstracao end-to-end (fontes sinteticas)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
& $vpy -m src.cli demo

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " PRONTO. Comandos do dia a dia (dentro desta pasta):" -ForegroundColor Cyan
Write-Host "   .\.venv\Scripts\python -m src.cli api     painel em http://localhost:8000" -ForegroundColor White
Write-Host "   .\.venv\Scripts\python -m src.cli scan    roda um ciclo de monitoramento" -ForegroundColor White
Write-Host "   .\.venv\Scripts\python -m pytest          roda os 44 testes" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
