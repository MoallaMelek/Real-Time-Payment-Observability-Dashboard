$ErrorActionPreference = "Stop"
$frontend = Join-Path (Split-Path -Parent $PSScriptRoot) "frontend"

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    throw "Frontend dependencies missing. Run .\scripts\setup.ps1 first."
}

Push-Location $frontend
try {
    npm run build
}
finally {
    Pop-Location
}
