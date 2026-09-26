param(
    [switch]$RestartBackend
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendPort = 8000

. (Join-Path $PSScriptRoot "port-utils.ps1")

& (Join-Path $PSScriptRoot "start-redis.ps1")

$listener = Get-ListeningTcpProcess -Port $backendPort
if ($listener) {
    if ($RestartBackend) {
        Write-Host "Stopping existing backend on port $backendPort (PID $($listener.OwningProcess))..."
        Stop-Process -Id $listener.OwningProcess -Force
        Start-Sleep -Seconds 1
    }
    else {
        Write-Host "Backend is already running on port $backendPort (PID $($listener.OwningProcess))."
        Write-Host "If it was started before Redis, rerun: .\scripts\run-local-stack.ps1 -RestartBackend"
    }
}

$listener = Get-ListeningTcpProcess -Port $backendPort
if (-not $listener) {
    Write-Host "Starting backend..."
    Start-Process -FilePath "powershell" -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "run-backend.ps1")
    ) -WorkingDirectory $root -WindowStyle Hidden
}

Start-Sleep -Seconds 2

Write-Host "Starting frontend..."
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "run-frontend.ps1")
) -WorkingDirectory $root -WindowStyle Hidden

Write-Host "Local stack requested: Redis, backend, frontend."
Write-Host "Frontend default: http://localhost:5173"
