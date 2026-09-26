function Get-ListeningTcpProcess {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
            if ($listener) {
                return $listener
            }
        }
    }
    catch {
        Write-Verbose "Get-NetTCPConnection failed; falling back to netstat.exe."
    }

    $lines = & netstat.exe -ano -p tcp 2>$null
    foreach ($line in $lines) {
        $parts = $line.Trim() -split "\s+"
        if ($parts.Count -lt 5 -or $parts[0] -ne "TCP" -or $parts[3] -ne "LISTENING") {
            continue
        }

        $localAddress = $parts[1]
        if ($localAddress -match ":(\d+)$" -and [int]$matches[1] -eq $Port) {
            return [pscustomobject]@{
                LocalPort      = $Port
                OwningProcess  = [int]$parts[4]
                ListenerSource = "netstat"
            }
        }
    }

    return $null
}
