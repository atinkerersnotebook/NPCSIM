param(
    [int]$Port = 8000,
    [string]$LanHost = ""
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
$bindHost = "127.0.0.1"
if ($LanHost) {
    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($LanHost, [ref]$address) -or
        $address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $LanHost -eq "0.0.0.0") {
        throw "LanHost must be the IPv4 address of this machine on the trusted LAN."
    }
    $bindHost = "0.0.0.0"
    Write-Host "NPCSIM admin (LAN): http://${LanHost}:$Port"
} else {
    Write-Host "NPCSIM admin: http://127.0.0.1:$Port"
}
$env:NPCSIM_LAN_HOST = $LanHost
& $python -m uvicorn npc_sim.app:app --host $bindHost --port $Port
exit $LASTEXITCODE
