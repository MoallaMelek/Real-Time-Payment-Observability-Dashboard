$ErrorActionPreference = "Stop"

$redisPort = 6379
$containerName = "payment-observability-dashboard-redis"
$composeArgs = @("compose", "up", "-d", "redis")

function Test-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-RedisPing {
    param(
        [string]$HostName = "127.0.0.1",
        [int]$Port = 6379
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(1000)) {
            throw "Redis port $Port is not reachable."
        }
        $client.EndConnect($connect)
        $stream = $client.GetStream()
        $payload = [System.Text.Encoding]::ASCII.GetBytes("*1`r`n`$4`r`nPING`r`n")
        $stream.Write($payload, 0, $payload.Length)
        $buffer = New-Object byte[] 256
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -le 0) {
            throw "Redis did not return a response."
        }
        $response = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read).Trim()
        if ($response -ne "+PONG") {
            throw "Unexpected Redis response: $response"
        }
        return "PONG"
    }
    finally {
        $client.Close()
    }
}

function Wait-RedisPing {
    param(
        [int]$Port = 6379,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            return Invoke-RedisPing -Port $Port
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    throw "Redis did not answer PING within $TimeoutSeconds seconds."
}

try {
    $pong = Invoke-RedisPing -Port $redisPort
    Write-Host "Redis is already available ($pong)."
    exit 0
}
catch {
    Write-Host "Redis is not responding on port $redisPort. Starting Docker Compose service..."
}

if (-not (Test-CommandAvailable "docker")) {
    throw "Docker is not installed or not available in PATH. Install Docker Desktop, then run: docker compose up -d redis"
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but not running. Start Docker Desktop, then run this script again."
}

$existingContainer = docker ps -a --filter "name=^/$containerName$" --format "{{.Names}}"
if ($existingContainer -eq $containerName) {
    $runningContainer = docker ps --filter "name=^/$containerName$" --filter "status=running" --format "{{.Names}}"
    if ($runningContainer -eq $containerName) {
        Write-Host "Existing Redis container '$containerName' is already running. Checking PING..."
    }
    else {
        Write-Host "Starting existing Redis container '$containerName'..."
        docker start $containerName | Out-Null
    }

    try {
        $pong = Wait-RedisPing -Port $redisPort -TimeoutSeconds 60
        Write-Host "Redis is available ($pong)."
        Write-Host "REDIS_URL=redis://localhost:6379/0"
        exit 0
    }
    catch {
        docker ps -a --filter "name=^/$containerName$"
        throw @"
Container '$containerName' already exists, but Redis is not reachable on localhost:$redisPort.

Check the container logs:
docker logs $containerName

If this old container was created with the wrong port mapping, remove or rename it manually, then rerun:
docker rm $containerName
.\scripts\start-redis.ps1
"@
    }
}

docker @composeArgs | Out-Host

$deadline = (Get-Date).AddSeconds(60)
do {
    $status = docker inspect --format "{{.State.Health.Status}}" $containerName 2>$null
    if ($status -eq "healthy") {
        $pong = Invoke-RedisPing -Port $redisPort
        Write-Host "Redis is available ($pong)."
        Write-Host "REDIS_URL=redis://localhost:6379/0"
        exit 0
    }
    try {
        $pong = Invoke-RedisPing -Port $redisPort
        Write-Host "Redis is available ($pong)."
        Write-Host "REDIS_URL=redis://localhost:6379/0"
        exit 0
    }
    catch {
        Start-Sleep -Seconds 2
        continue
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

docker compose ps redis
docker compose logs --tail 40 redis
throw "Redis container did not become healthy within 60 seconds."
