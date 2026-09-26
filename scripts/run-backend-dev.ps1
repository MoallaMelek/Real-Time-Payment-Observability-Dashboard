$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$port = 8000

. (Join-Path $PSScriptRoot "port-utils.ps1")

if (-not (Test-Path $python)) {
    throw "Backend environment missing. Run .\scripts\setup.ps1 first."
}

$listener = Get-ListeningTcpProcess -Port $port
if ($listener) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    Write-Host "Port $port is already in use by PID $($listener.OwningProcess)."
    if ($process) {
        Write-Host "Command: $($process.CommandLine)"
    }
    Write-Host "Dev backend not started to avoid a duplicate Windows process."
    exit 1
}

Push-Location $backend
try {
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $port --reload
}
finally {
    Pop-Location
}
