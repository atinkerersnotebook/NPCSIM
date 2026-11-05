param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing virtual environment. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}
if (-not (Test-Path (Join-Path $PSScriptRoot "ui\dist\index.html"))) {
    throw "Missing UI build. Run: Set-Location ui; npm install; npm run build"
}
Write-Host "NPCSIM admin: http://127.0.0.1:$Port"
& $python -m uvicorn npc_sim.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
