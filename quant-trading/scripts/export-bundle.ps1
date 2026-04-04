# ────────────────────────────────────────────────────────────────────────────────
# quant-trading export — 把配置 + 数据 + Python 策略打包成一个可迁移的 zip
#
# 用法:
#   .\quant-trading\scripts\export-bundle.ps1 [输出文件路径]
#
# 默认输出: ~/quant-trading-bundle-<日期>.zip
#
# 打包内容:
#   1. openclaw.json 中的 quant-trading 插件配置
#   2. ~/.openclaw/quant-trading/ 运行时数据 (signals, stock-list, portfolio, orders …)
#   3. ~/.openclaw/extensions/quant-trading/cache/ 缓存数据库 (stock_data.db, financial_data.db)
#   4. ~/.openclaw/workspace/ 人设文件 (IDENTITY, USER, SOUL, TOOLS …)
#   5. ~/.openclaw/identity/ 设备密钥
#   6. ~/.openclaw/cron/ 定时任务
#   7. Python 策略代码 (strategies/, screeners/, engines/, data_providers/, brokers/, cache/)
#   8. Python 依赖清单 (requirements.txt)
#   9. 插件元信息 (openclaw.plugin.json, package.json)
# ────────────────────────────────────────────────────────────────────────────────
param(
    [string]$Output
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExtDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)

$DateTag = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $Output) {
    $Output = Join-Path $HOME "quant-trading-bundle-${DateTag}.zip"
}

# Create staging directory
$Staging = Join-Path $env:TEMP "qt-export-$DateTag"
$Bundle = Join-Path $Staging 'quant-trading-bundle'

foreach ($sub in 'config', 'data', 'cache', 'openclaw-home', 'python', 'meta') {
    New-Item -ItemType Directory -Path (Join-Path $Bundle $sub) -Force | Out-Null
}

# Cleanup on exit
$cleanupStaging = $Staging
trap { if (Test-Path $cleanupStaging) { Remove-Item $cleanupStaging -Recurse -Force -ErrorAction SilentlyContinue } }

Write-Host "`u{1F4E6} Quant Trading Export"
Write-Host "───────────────────────"

# ── 1. 插件配置 ──────────────────────────────────────────────────────────────
$ConfigFile = Join-Path $RepoRoot 'config-custom\openclaw.json'
if (Test-Path $ConfigFile) {
    Copy-Item $ConfigFile (Join-Path $Bundle 'config\openclaw.json')
    Write-Host "✓ 配置文件  config-custom\openclaw.json"
} else {
    Write-Host "⚠ 未找到 config-custom\openclaw.json，跳过"
}

$HomeConfig = Join-Path $HOME '.openclaw\openclaw.json'
if (Test-Path $HomeConfig) {
    Copy-Item $HomeConfig (Join-Path $Bundle 'config\openclaw-home.json')
    Write-Host "✓ 配置文件  ~/.openclaw/openclaw.json"
}

# ── 2. 运行时数据 ────────────────────────────────────────────────────────────
$DataDir = if ($env:QUANT_DATA_DIR) { $env:QUANT_DATA_DIR } else { Join-Path $HOME '.openclaw\quant-trading' }
if (Test-Path $DataDir) {
    Copy-Item "$DataDir\*" (Join-Path $Bundle 'data') -Recurse -Force -ErrorAction SilentlyContinue
    $fileCount = (Get-ChildItem (Join-Path $Bundle 'data') -Recurse -File).Count
    Write-Host "✓ 运行时数据  $DataDir  ($fileCount 个文件)"
} else {
    Write-Host "⚠ 数据目录 $DataDir 不存在，跳过"
}

# ── 3. 缓存数据库 ────────────────────────────────────────────────────────────
$CacheDir = Join-Path $HOME '.openclaw\extensions\quant-trading\cache'
if (Test-Path $CacheDir) {
    # Copy .db files (skip -shm and -wal if present)
    Get-ChildItem $CacheDir -Filter '*.db' -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $Bundle 'cache')
    }
    # Copy JSON caches
    Get-ChildItem $CacheDir -Filter '*.json' -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $Bundle 'cache')
    }
    $cacheCount = (Get-ChildItem (Join-Path $Bundle 'cache') -File -ErrorAction SilentlyContinue).Count
    $cacheSize = '{0:N2} MB' -f ((Get-ChildItem (Join-Path $Bundle 'cache') -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB)
    Write-Host "✓ 缓存数据库  $CacheDir  ($cacheCount 个文件, $cacheSize)"
} else {
    Write-Host "⚠ 缓存目录 $CacheDir 不存在，跳过"
}

# ── 4. ~/.openclaw 其他有用目录 ──────────────────────────────────────────────
$OpenclawHome = Join-Path $HOME '.openclaw'

# workspace/ — AI 人设文件
$WorkspaceDir = Join-Path $OpenclawHome 'workspace'
if (Test-Path $WorkspaceDir) {
    $destWs = Join-Path $Bundle 'openclaw-home\workspace'
    New-Item -ItemType Directory -Path $destWs -Force | Out-Null
    Copy-Item "$WorkspaceDir\*" $destWs -Recurse -Force -Exclude '.DS_Store'
    $wsCount = (Get-ChildItem $destWs -File -Recurse).Count
    Write-Host "✓ Workspace 人设  $WorkspaceDir  ($wsCount 个文件)"
}

# identity/ — 设备身份密钥
$IdentityDir = Join-Path $OpenclawHome 'identity'
if (Test-Path $IdentityDir) {
    $destId = Join-Path $Bundle 'openclaw-home\identity'
    New-Item -ItemType Directory -Path $destId -Force | Out-Null
    Copy-Item "$IdentityDir\*" $destId -Recurse -Force
    Write-Host "✓ 设备身份  $IdentityDir"
}

# cron/ — 定时任务
$CronDir = Join-Path $OpenclawHome 'cron'
if (Test-Path $CronDir) {
    $destCron = Join-Path $Bundle 'openclaw-home\cron'
    New-Item -ItemType Directory -Path $destCron -Force | Out-Null
    Get-ChildItem $CronDir -Filter '*.json' -File | ForEach-Object {
        Copy-Item $_.FullName $destCron
    }
    Write-Host "✓ 定时任务  $CronDir"
}

# feishu/ — 飞书去重状态
$FeishuDir = Join-Path $OpenclawHome 'feishu'
if (Test-Path $FeishuDir) {
    $destFeishu = Join-Path $Bundle 'openclaw-home\feishu'
    New-Item -ItemType Directory -Path $destFeishu -Force | Out-Null
    Copy-Item "$FeishuDir\*" $destFeishu -Recurse -Force -Exclude '.DS_Store'
    Write-Host "✓ 飞书状态  $FeishuDir"
}

# ── 5. Python 策略代码 ───────────────────────────────────────────────────────
$PythonDir = Join-Path $ExtDir 'python'
if (Test-Path $PythonDir) {
    $destPy = Join-Path $Bundle 'python'
    # Copy everything except __pycache__ and .pyc
    Get-ChildItem $PythonDir -Recurse |
        Where-Object { $_.FullName -notmatch '__pycache__' -and $_.Extension -ne '.pyc' -and $_.Name -ne '.DS_Store' } |
        ForEach-Object {
            $relPath = $_.FullName.Substring($PythonDir.Length)
            $targetPath = Join-Path $destPy $relPath
            if ($_.PSIsContainer) {
                New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
            } else {
                $parentDir = Split-Path -Parent $targetPath
                if (-not (Test-Path $parentDir)) {
                    New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
                }
                Copy-Item $_.FullName $targetPath -Force
            }
        }
    $pyCount = (Get-ChildItem $destPy -Filter '*.py' -Recurse -ErrorAction SilentlyContinue).Count
    Write-Host "✓ Python 策略  $pyCount 个 .py 文件"
} else {
    Write-Host "⚠ Python 目录 $PythonDir 不存在，跳过"
}

# ── 6. 插件元信息 ────────────────────────────────────────────────────────────
foreach ($f in 'openclaw.plugin.json', 'package.json', 'README.md', 'index.ts') {
    $src = Join-Path $ExtDir $f
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $Bundle 'meta')
    }
}

# TypeScript 源码
$SrcDir = Join-Path $ExtDir 'src'
if (Test-Path $SrcDir) {
    $destSrc = Join-Path $Bundle 'meta\src'
    New-Item -ItemType Directory -Path $destSrc -Force | Out-Null
    Get-ChildItem $SrcDir -Recurse |
        Where-Object { $_.Extension -ne '.test.ts' -and $_.FullName -notmatch '__pycache__' -and $_.Name -ne '.DS_Store' } |
        ForEach-Object {
            $relPath = $_.FullName.Substring($SrcDir.Length)
            $targetPath = Join-Path $destSrc $relPath
            if ($_.PSIsContainer) {
                New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
            } else {
                $parentDir = Split-Path -Parent $targetPath
                if (-not (Test-Path $parentDir)) {
                    New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
                }
                Copy-Item $_.FullName $targetPath -Force
            }
        }
    Write-Host "✓ TypeScript 源码  meta\src\"
}

# ── 7. 生成 manifest ────────────────────────────────────────────────────────
$manifest = @{
    bundleVersion = 1
    exportDate    = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    exportHost    = $env:COMPUTERNAME
    exportUser    = $env:USERNAME
    contents      = @{
        config         = '插件配置 (openclaw.json)'
        data           = '运行时数据 (signals, stock-list, portfolio, orders …)'
        cache          = 'SQLite 缓存数据库 (stock_data.db, financial_data.db)'
        'openclaw-home' = '~/.openclaw 目录 (workspace 人设, identity 密钥, cron 定时任务, feishu 状态)'
        python         = 'Python 策略/选股/引擎代码'
        meta           = '插件元信息 + TypeScript 源码'
    }
    importScript  = '使用 import-bundle.ps1 导入'
} | ConvertTo-Json -Depth 3

$manifest | Out-File (Join-Path $Bundle 'MANIFEST.json') -Encoding utf8

# ── 打包 (zip) ───────────────────────────────────────────────────────────────
if (Test-Path $Output) {
    Remove-Item $Output -Force
}
Compress-Archive -Path $Bundle -DestinationPath $Output -CompressionLevel Optimal

$size = '{0:N2} MB' -f ((Get-Item $Output).Length / 1MB)
Write-Host ""
Write-Host "✅ 导出完成: $Output ($size)"
Write-Host ""
Write-Host "在目标机器上导入:"
Write-Host "  .\quant-trading\scripts\import-bundle.ps1 $Output"

# Cleanup staging
Remove-Item $Staging -Recurse -Force -ErrorAction SilentlyContinue
