# Start the QT gateway in the background
param(
    [int]$Port = $env:QT_GATEWAY_PORT ? [int]$env:QT_GATEWAY_PORT : 18889
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ProjectDir 'logs'

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Check if something is already listening on the port
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pids = ($existing.OwningProcess | Sort-Object -Unique) -join ', '
    Write-Host "⚠️  Port $Port is already in use (PID: $pids)"
    Write-Host "   Run .\scripts\gateway-down.ps1 first, or set `$env:QT_GATEWAY_PORT to use a different port."
    exit 1
}

$LogFile = Join-Path $LogDir "gateway-${Port}.log"
$PidFile = Join-Path $LogDir "gateway-${Port}.pid"

Write-Host "Starting QT gateway on port $Port ..."

Push-Location $ProjectDir
try {
    $process = Start-Process -FilePath 'pnpm' `
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
        Write-Host "✅ Gateway started (PID: $($process.Id), port: $Port)"
        Write-Host "   Log: $LogFile"
    } else {
        Write-Host "❌ Gateway failed to start. Check log: $LogFile"
        exit 1
    }
} finally {
    Pop-Location
}
