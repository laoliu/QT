# 一键启动 QT 交易系统 + 注册 Windows 开机自启
# 
# 用法:
#   .\qt-autostart.ps1              # 启动系统
#   .\qt-autostart.ps1 -Install     # 注册开机自启（添加到 Windows 启动文件夹）
#   .\qt-autostart.ps1 -Uninstall   # 取消开机自启
#   .\qt-autostart.ps1 -Status      # 检查运行状态
#   .\qt-autostart.ps1 -Stop        # 停止本项目的 18891 服务及其子进程
#   .\qt-autostart.ps1 -Restart     # 停止后重新启动
#   .\qt-autostart.ps1 -Restart -WhatIf # 仅预览，不执行
# 停止使用 Windows taskkill /T /F；不会停止独立运行的 QMT 桥接服务。
[CmdletBinding(SupportsShouldProcess = $true, DefaultParameterSetName = 'Start')]
param(
    [Parameter(ParameterSetName = 'Install')]
    [switch]$Install,
    [Parameter(ParameterSetName = 'Uninstall')]
    [switch]$Uninstall,
    [Parameter(ParameterSetName = 'Status')]
    [switch]$Status,
    [Parameter(ParameterSetName = 'Stop')]
    [switch]$Stop,
    [Parameter(ParameterSetName = 'Restart')]
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$StartupFolder = [System.IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs\Startup')
$ShortcutName = 'QT-Trading-AutoStart.lnk'
$ShortcutPath = Join-Path $StartupFolder $ShortcutName
$Port = 18891
$standalone = Join-Path $ProjectDir 'quant-trading\standalone.ts'

function Get-QTServiceProcess {
    param([int]$ServicePort, [string]$EntryPoint)

    $entryPattern = '(?i)(?:^|[\s"])' + [regex]::Escape($EntryPoint.Replace('/', '\')) + '(?=$|[\s"])'
    $listenerIds = @(
        Get-NetTCPConnection -LocalPort $ServicePort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    foreach ($listenerId in $listenerIds) {
        $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerId"
        if ($null -eq $serviceProcess) { continue }
        if ($serviceProcess.Name -ne 'node.exe' -or
            -not $serviceProcess.CommandLine -or
            $serviceProcess.CommandLine.Replace('/', '\') -notmatch $entryPattern) {
            throw "Port $ServicePort is owned by PID $listenerId, which is not this project's QT service. Refusing to stop it."
        }

        # Include the tsx/pnpm Node wrapper so it cannot keep the service alive.
        $rootProcess = $serviceProcess
        $visited = @{}
        while ($rootProcess.ParentProcessId -gt 4 -and -not $visited.ContainsKey([int]$rootProcess.ParentProcessId)) {
            $visited[[int]$rootProcess.ProcessId] = $true
            $parentProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($rootProcess.ParentProcessId)"
            if ($null -eq $parentProcess -or $parentProcess.Name -ne 'node.exe' -or
                -not $parentProcess.CommandLine -or
                $parentProcess.CommandLine.Replace('/', '\') -notmatch $entryPattern -or
                $parentProcess.CreationDate -gt $rootProcess.CreationDate) { break }
            $rootProcess = $parentProcess
        }
        $rootProcess
    }
}

function Stop-QTService {
    param([int]$ServicePort, [object[]]$ServiceProcesses)

    if ($ServiceProcesses.Count -eq 0) {
        Write-Host "QT service is already stopped (port $ServicePort)."
        return
    }
    foreach ($serviceProcess in ($ServiceProcesses | Sort-Object ProcessId -Unique)) {
        # Recheck identity before terminating a PID discovered earlier.
        $currentProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($serviceProcess.ProcessId)"
        if ($null -eq $currentProcess) { continue }
        if ($currentProcess.CreationDate -ne $serviceProcess.CreationDate -or
            $currentProcess.CommandLine -ne $serviceProcess.CommandLine) {
            throw "PID $($serviceProcess.ProcessId) changed ownership; refusing to stop it."
        }
        Write-Host "Stopping QT service tree (PID $($serviceProcess.ProcessId), port $ServicePort)..."
        & taskkill.exe /PID $serviceProcess.ProcessId /T /F | Out-Host
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $serviceProcess.ProcessId -ErrorAction SilentlyContinue)) {
            throw "Failed to stop QT service PID $($serviceProcess.ProcessId)."
        }
    }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        $remaining = @(Get-NetTCPConnection -LocalPort $ServicePort -State Listen -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            Write-Host "QT service stopped (port $ServicePort)."
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "Port $ServicePort is still listening; startup was cancelled."
}

if ($Stop -or $Restart) {
    if ($Restart) {
        if (-not (Test-Path -LiteralPath $standalone -PathType Leaf)) {
            throw "QT entrypoint not found: $standalone"
        }
        if (-not (Get-Command pnpm.cmd -ErrorAction SilentlyContinue)) {
            throw 'pnpm was not found; restart was cancelled before stopping the service.'
        }
    }
    $serviceProcesses = @(Get-QTServiceProcess -ServicePort $Port -EntryPoint $standalone)
    $operation = if ($Restart) { 'Restart QT service' } else { 'Stop QT service' }
    if (-not $PSCmdlet.ShouldProcess("$standalone (port $Port)", $operation)) { exit 0 }
    Stop-QTService -ServicePort $Port -ServiceProcesses $serviceProcesses
    if ($Stop) { exit 0 }
}

# ── Install: 注册开机自启 ────────────────────────────────
if ($Install) {
    if (-not $PSCmdlet.ShouldProcess($ShortcutPath, 'Install startup shortcut')) { exit 0 }
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = 'powershell.exe'
    $Shortcut.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptDir\qt-autostart.ps1`""
    $Shortcut.WorkingDirectory = $ScriptDir
    $Shortcut.Description = 'QT Trading System Auto-Start'
    $Shortcut.Save()
    Write-Host "✅ 已注册开机自启"
    Write-Host "   快捷方式: $ShortcutPath"
    Write-Host "   取消: .\qt-autostart.ps1 -Uninstall"
    exit 0
}

# ── Uninstall: 取消开机自启 ───────────────────────────────
if ($Uninstall) {
    if (-not $PSCmdlet.ShouldProcess($ShortcutPath, 'Remove startup shortcut')) { exit 0 }
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "✅ 已取消开机自启"
    } else {
        Write-Host "⚠️  未注册开机自启"
    }
    exit 0
}

# ── Status: 检查运行状态 ─────────────────────────────────
if ($Status) {
    $Port = 18891
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        $procId = ($listening.OwningProcess | Sort-Object -Unique | Select-Object -First 1)
        Write-Host "✅ 交易系统运行中 (PID: $procId, 端口: $Port)"
        Write-Host "   管理后台: http://127.0.0.1:${Port}/quant/admin/ui"
        Write-Host "   自动交易: http://127.0.0.1:${Port}/quant/admin/auto-trade"
        
        # Check auto-trade config
        $configPath = Join-Path $env:USERPROFILE 'QT\\data\\auto-trade-config.json'
        if (Test-Path $configPath) {
            $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
            $mode = if ($cfg.enabled) { '✅ 已启用' } else { '⏸️ 已停止' }
            $dryRun = if ($cfg.dryRun) { '🧪 试运行' } else { '⚡ 实盘' }
            Write-Host "   自动交易: $mode ($dryRun)"
        }
    } else {
        Write-Host "❌ 交易系统未运行"
        Write-Host "   启动: .\qt-autostart.ps1"
    }
    
    $autoStartInstalled = Test-Path $ShortcutPath
    Write-Host "   开机自启: $(if ($autoStartInstalled) { '✅ 已注册' } else { '❌ 未注册' })"
    exit 0
}

# ── 主流程：启动系统 ─────────────────────────────────────

Write-Host ""
Write-Host "╔══════════════════════════════════════╗"
Write-Host "║     🤖 QT 交易系统启动中...          ║"
Write-Host "╚══════════════════════════════════════╝"
Write-Host ""

# 1. 检查是否已在运行
$Port = 18891
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "✅ 系统已在运行 (端口 $Port)"
    Write-Host "   管理后台: http://127.0.0.1:${Port}/quant/admin/ui"
    exit 0
}

# 2. 启动独立服务
$pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpm) {
    throw '未找到 pnpm，请先安装 Node.js/pnpm。'
}
if (-not $Restart -and -not $PSCmdlet.ShouldProcess($standalone, 'Start QT service')) { exit 0 }
Start-Process -FilePath $pnpm.Source -ArgumentList @('exec', 'tsx', "`"$standalone`"") -WorkingDirectory $ProjectDir -WindowStyle Hidden

# 4. 等待服务就绪
$maxWait = 15
$waited = 0
$ready = $false
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 1
    $waited++
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/quant/admin/api/auto-trade/status" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if (-not $ready) {
    Write-Host "⚠️  服务可能还在启动，请稍后检查"
    exit 1
}

# 5. 显示启动摘要
try {
    $statusResp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/quant/admin/api/auto-trade/status" -UseBasicParsing -TimeoutSec 5
    $status = $statusResp.Content | ConvertFrom-Json
    
    Write-Host ""
    Write-Host "════════════════════════════════════════"
    Write-Host "  ✅ QT 交易系统已启动"
    Write-Host "════════════════════════════════════════"
    Write-Host ""
    Write-Host "  📊 管理后台:  http://127.0.0.1:${Port}/quant/admin/ui"
    Write-Host "  🤖 自动交易:  http://127.0.0.1:${Port}/quant/admin/auto-trade"
    Write-Host "  📡 条件单:    http://127.0.0.1:${Port}/quant/admin/conditional-orders"
    Write-Host ""
    
    $autoEnabled = $status.enabled
    $dryRun = $status.config.dryRun
    $mode = if ($dryRun) { '🧪 试运行' } else { '⚡ 实盘' }
    
    if ($autoEnabled) {
        Write-Host "  🤖 自动交易:  ✅ 已启用 ($mode)"
        Write-Host "     筛选定时:  $($status.config.screenCron)"
        Write-Host "     执行定时:  $($status.config.executeCron)"
    } else {
        Write-Host "  🤖 自动交易:  ⏸️ 未启用"
        Write-Host "     前往管理后台启用，或运行:"
        Write-Host "     Invoke-WebRequest -Method POST -Uri 'http://127.0.0.1:${Port}/quant/admin/api/auto-trade/config' -ContentType 'application/json' -Body '{`"enabled`":true}'"
    }
    
    # Check conditional orders
    try {
        $coResp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/quant/admin/api/conditional-orders?active=true" -UseBasicParsing -TimeoutSec 5
        $coData = $coResp.Content | ConvertFrom-Json
        $coCount = ($coData.orders | Measure-Object).Count
        Write-Host "  📡 条件单:    $coCount 个活跃"
    } catch { }
    
    Write-Host ""
} catch {
    Write-Host "✅ 系统已启动，管理后台: http://127.0.0.1:${Port}/quant/admin/ui"
}
