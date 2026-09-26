$ErrorActionPreference = "Stop"
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Backend environment missing. Run .\scripts\setup.ps1 first."
}

Push-Location $backend
try {
    & $python -m unittest discover -s tests -v
}
finally {
    Pop-Location
}
