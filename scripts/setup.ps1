$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $root "backend")
try {
    if (-not (Test-Path ".venv")) {
        python -m venv .venv
    }
    & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
}
finally {
    Pop-Location
}

Push-Location (Join-Path $root "frontend")
try {
    npm ci
}
finally {
    Pop-Location
}

Write-Host "Project dependencies installed successfully."
