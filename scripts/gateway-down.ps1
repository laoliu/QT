# Stop the QT gateway
param(
    [int]$Port = $env:QT_GATEWAY_PORT ? [int]$env:QT_GATEWAY_PORT : 18889
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ProjectDir 'logs'
$PidFile = Join-Path $LogDir "gateway-${Port}.pid"

Write-Host "Stopping QT gateway on port $Port ..."

$stopped = $false

# 1) Try graceful stop via openclaw CLI
Push-Location $ProjectDir
try {
    pnpm openclaw gateway stop 2>$null
    if ($LASTEXITCODE -eq 0) { $stopped = $true }
} catch {
    # ignore
} finally {
    Pop-Location
}

# 2) Kill by PID file
if (Test-Path $PidFile) {
    $gatewayPid = (Get-Content $PidFile -Raw).Trim()
    if ($gatewayPid -match '^\d+$') {
        $proc = Get-Process -Id ([int]$gatewayPid) -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id ([int]$gatewayPid) -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# 3) Kill anything still listening on the port
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($conn in $conns) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    $stopped = $true
}

if ($stopped) {
    Write-Host "✅ Gateway stopped."
} else {
    Write-Host "ℹ️  No gateway was running on port $Port."
}
