# Stop the QT gateway
# Usage: .\gateway-down.ps1 [-Lobster] [-PyGateway] [-StandalonePort 18891] [-Port 18889] [-PyPort 8000]
#   Default      Stop the standalone HTTP server
#   -Lobster     Stop the OpenClaw (龙虾) Node.js gateway instead
#   -PyGateway   Stop the Python FastAPI gateway
param(
    [int]$Port = $env:QT_GATEWAY_PORT ? [int]$env:QT_GATEWAY_PORT : 18889,
    [int]$PyPort = $env:QT_PY_GATEWAY_PORT ? [int]$env:QT_PY_GATEWAY_PORT : 8001,
    [int]$StandalonePort = $env:QT_STANDALONE_PORT ? [int]$env:QT_STANDALONE_PORT : 18891,
    [switch]$Lobster,
    [switch]$PyGateway
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ProjectDir 'logs'

# ── OpenClaw (龙虾) mode ──────────────────────────────────────
if ($Lobster) {
    $PidFile = Join-Path $LogDir "gateway-${Port}.pid"

    Write-Host "Stopping OpenClaw (龙虾) gateway on port $Port ..."

    $stopped = $false

    # 1) Try graceful stop via openclaw CLI (only if something is listening)
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        Push-Location $ProjectDir
        try {
            pnpm openclaw gateway stop 2>$null
            if ($LASTEXITCODE -eq 0) { $stopped = $true }
        } catch {
            # ignore
        } finally {
            Pop-Location
        }
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
        Write-Host "✅ OpenClaw gateway stopped."
    } else {
        Write-Host "ℹ️  No OpenClaw gateway was running on port $Port."
    }
    exit 0
}

# ── Python FastAPI mode ────────────────────────────────────────
if ($PyGateway) {
    $PidFile = Join-Path $LogDir "py-gateway-${PyPort}.pid"

    Write-Host "Stopping Python FastAPI gateway on port $PyPort ..."

    $stopped = $false

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

    $conns = Get-NetTCPConnection -LocalPort $PyPort -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        $stopped = $true
    }

    if ($stopped) {
        Write-Host "✅ Python gateway stopped."
    } else {
        Write-Host "ℹ️  No Python gateway was running on port $PyPort."
    }
    exit 0
}

# ── Standalone mode (default) ─────────────────────────────────
$PidFile = Join-Path $LogDir "standalone-${StandalonePort}.pid"

Write-Host "Stopping standalone HTTP server on port $StandalonePort ..."

$stopped = $false

if (Test-Path $PidFile) {
    $saPid = (Get-Content $PidFile -Raw).Trim()
    if ($saPid -match '^\d+$') {
        $proc = Get-Process -Id ([int]$saPid) -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id ([int]$saPid) -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$conns = Get-NetTCPConnection -LocalPort $StandalonePort -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($conn in $conns) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    $stopped = $true
}

if ($stopped) {
    Write-Host "✅ Standalone server stopped."
} else {
    Write-Host "ℹ️  No standalone server was running on port $StandalonePort."
}
