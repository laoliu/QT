# Start the QT gateway in the background
# Usage: .\gateway-up.ps1 [-Lobster] [-PyGateway] [-StandalonePort 18891] [-Port 18889] [-PyPort 8000]
#   Default      Start the quant-trading standalone HTTP server (no 龙虾 needed)
#   -Lobster     Start the OpenClaw (龙虾) Node.js gateway instead
#   -PyGateway   Start the Python FastAPI gateway (uvicorn) on PyPort
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
$PythonDir = Join-Path $ProjectDir 'quant-trading' 'python'

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# ── OpenClaw (龙虾) mode ──────────────────────────────────────
if ($Lobster) {
    # Check if something is already listening on the port
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        $pids = ($existing.OwningProcess | Sort-Object -Unique) -join ', '
        Write-Host "⚠️  Port $Port is already in use (PID: $pids)"
        Write-Host "   Run .\scripts\gateway-down.ps1 -Lobster first, or set `$env:QT_GATEWAY_PORT to use a different port."
        exit 1
    }

    $LogFile = Join-Path $LogDir "gateway-${Port}.log"
    $PidFile = Join-Path $LogDir "gateway-${Port}.pid"

    Write-Host "Starting OpenClaw (龙虾) gateway on port $Port ..."

    Push-Location $ProjectDir
    try {
        # Resolve pnpm executable — prefer .cmd/.exe over .ps1 for Start-Process compatibility
        $pnpmCmd = (Get-Command pnpm.cmd -ErrorAction SilentlyContinue)?.Source ??
                   (Get-Command pnpm.exe -ErrorAction SilentlyContinue)?.Source ??
                   'pnpm.cmd'
        $process = Start-Process -FilePath $pnpmCmd `
            -ArgumentList "openclaw gateway run --bind loopback --port $Port --force" `
            -WindowStyle Hidden `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError (Join-Path $LogDir "gateway-${Port}-err.log") `
            -PassThru

        $process.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline

        # Wait briefly and verify the process is still alive
        Start-Sleep -Seconds 2

        $check = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($check -and -not $check.HasExited) {
            Write-Host "✅ OpenClaw gateway started (PID: $($process.Id), port: $Port)"
            Write-Host "   Log: $LogFile"
        } else {
            Write-Host "❌ OpenClaw gateway failed to start. Check log: $LogFile"
            exit 1
        }
    } finally {
        Pop-Location
    }
    exit 0
}

# ── Python FastAPI mode ────────────────────────────────────────
if ($PyGateway) {
    $existing = Get-NetTCPConnection -LocalPort $PyPort -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        $pids = ($existing.OwningProcess | Sort-Object -Unique) -join ', '
        Write-Host "⚠️  Port $PyPort is already in use (PID: $pids)"
        Write-Host "   Run .\scripts\gateway-down.ps1 -PyGateway first, or set `$env:QT_PY_GATEWAY_PORT to use a different port."
        exit 1
    }

    $LogFile = Join-Path $LogDir "py-gateway-${PyPort}.log"
    $PidFile = Join-Path $LogDir "py-gateway-${PyPort}.pid"

    Write-Host "Starting Python FastAPI gateway on port $PyPort ..."

    Push-Location $PythonDir
    try {
        $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)?.Source ?? 'python'
        $process = Start-Process -FilePath $pythonCmd `
            -ArgumentList "-m uvicorn aipha.api.app:app --host 127.0.0.1 --port $PyPort" `
            -WindowStyle Hidden `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError (Join-Path $LogDir "py-gateway-${PyPort}-err.log") `
            -PassThru

        $process.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline

        Start-Sleep -Seconds 2

        $check = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($check -and -not $check.HasExited) {
            Write-Host "✅ Python gateway started (PID: $($process.Id), port: $PyPort)"
            Write-Host "   Log: $LogFile"
            Write-Host "   API docs: http://127.0.0.1:${PyPort}/api/docs"
        } else {
            Write-Host "❌ Python gateway failed to start. Check log: $LogFile"
            exit 1
        }
    } finally {
        Pop-Location
    }
    exit 0
}

# ── Standalone mode (default, no 龙虾 needed) ─────────────────
$existing = Get-NetTCPConnection -LocalPort $StandalonePort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pids = ($existing.OwningProcess | Sort-Object -Unique) -join ', '
    Write-Host "⚠️  Port $StandalonePort is already in use (PID: $pids)"
    Write-Host "   Run .\scripts\gateway-down.ps1 first, or set `$env:QT_STANDALONE_PORT to use a different port."
    exit 1
}

$LogFile = Join-Path $LogDir "standalone-${StandalonePort}.log"
$PidFile = Join-Path $LogDir "standalone-${StandalonePort}.pid"

Write-Host "Starting quant-trading standalone HTTP server on port $StandalonePort ..."

$tsxCmd = Join-Path $ProjectDir 'node_modules' '.bin' 'tsx.cmd'
if (-not (Test-Path $tsxCmd)) {
    $tsxCmd = (Get-Command tsx.cmd -ErrorAction SilentlyContinue)?.Source ?? 'npx.cmd'
}

Push-Location $ProjectDir
try {
    if ($tsxCmd -like '*npx*') {
        $tsxArgs = "tsx quant-trading/standalone.ts --port $StandalonePort"
    } else {
        $tsxArgs = "quant-trading/standalone.ts --port $StandalonePort"
    }

    $process = Start-Process -FilePath $tsxCmd `
        -ArgumentList $tsxArgs `
        -WindowStyle Hidden `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError (Join-Path $LogDir "standalone-${StandalonePort}-err.log") `
        -PassThru

    $process.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline

    Start-Sleep -Seconds 3

    $check = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if ($check -and -not $check.HasExited) {
        Write-Host "✅ Standalone server started (PID: $($process.Id), port: $StandalonePort)"
        Write-Host "   Admin UI: http://127.0.0.1:${StandalonePort}/quant/admin/ui"
        Write-Host "   Log: $LogFile"
    } else {
        Write-Host "❌ Standalone server failed to start. Check log: $LogFile"
        exit 1
    }
} finally {
    Pop-Location
}
