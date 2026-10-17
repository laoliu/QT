param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$Port
)

$ErrorActionPreference = 'Stop'

try {
    $listenerIds = @(
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
} catch {
    Write-Error "Cannot inspect TCP port ${Port}: $($_.Exception.Message)"
    exit 1
}

if ($listenerIds.Count -eq 0) {
    Write-Host "No TCP listener found on port $Port."
    exit 0
}

foreach ($listenerId in $listenerIds) {
    $processId = [int]$listenerId
    if ($processId -le 4) {
        Write-Error "Port $Port is owned by protected system PID $processId; refusing to terminate it."
        exit 1
    }

    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Host "Listener PID $processId already exited."
        continue
    }

    Write-Host "Stopping listener on TCP port ${Port}: PID $processId ($($process.ProcessName))."
    try {
        Stop-Process -Id $processId -Force -ErrorAction Stop
        Wait-Process -Id $processId -Timeout 5 -ErrorAction SilentlyContinue
    } catch {
        Write-Error "Failed to stop PID $processId on port ${Port}: $($_.Exception.Message)"
        exit 1
    }
}

$remaining = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
)
if ($remaining.Count -gt 0) {
    Write-Error "A TCP listener is still present on port $Port."
    exit 1
}

Write-Host "TCP port $Port is clear."
