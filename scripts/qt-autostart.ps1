# 一键启动 QT 交易系统 + 注册 Windows 开机自启
# 
# 用法:
#   .\qt-autostart.ps1              # 启动系统
#   .\qt-autostart.ps1 -Install     # 注册开机自启（添加到 Windows 启动文件夹）
#   .\qt-autostart.ps1 -Uninstall   # 取消开机自启
#   .\qt-autostart.ps1 -Status      # 检查运行状态
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$StartupFolder = [System.IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs\Startup')
$ShortcutName = 'QT-Trading-AutoStart.lnk'
$ShortcutPath = Join-Path $StartupFolder $ShortcutName

# ── Install: 注册开机自启 ────────────────────────────────
if ($Install) {
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
$standalone = Join-Path $ProjectDir 'quant-trading\standalone.ts'
$pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpm) {
    throw '未找到 pnpm，请先安装 Node.js/pnpm。'
}
Start-Process -FilePath $pnpm.Source -ArgumentList @('exec', 'tsx', $standalone) -WorkingDirectory $ProjectDir -WindowStyle Hidden

# 4. 等待服务就绪
$maxWait = 15
$waited = 0
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 1
    $waited++
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/quant/admin/api/auto-trade/status" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) { break }
    } catch { }
}

if ($waited -ge $maxWait) {
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
