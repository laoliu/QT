# ────────────────────────────────────────────────────────────────────────────────
# quant-trading import — 从 export-bundle.ps1 导出的 zip 恢复配置和数据
#
# 用法:
#   .\quant-trading\scripts\import-bundle.ps1 <bundle.zip> [选项]
#
# 选项:
#   -ConfigOnly     只恢复配置，不覆盖运行时数据
#   -DataOnly       只恢复运行时数据，不覆盖配置
#   -PythonOnly     只恢复 Python 策略代码
#   -NoBackup       不备份现有文件
#   -DryRun         只显示会做什么，不实际操作
#   -Force          不询问确认，直接覆盖
# ────────────────────────────────────────────────────────────────────────────────
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BundleFile,
    [switch]$ConfigOnly,
    [switch]$DataOnly,
    [switch]$PythonOnly,
    [switch]$NoBackup,
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExtDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)

if (-not (Test-Path $BundleFile)) {
    Write-Host "❌ 文件不存在: $BundleFile"
    exit 1
}

$DateTag = Get-Date -Format 'yyyyMMdd-HHmmss'

# ── 解包 ─────────────────────────────────────────────────────────────────────
$Staging = Join-Path $env:TEMP "qt-import-$DateTag"
New-Item -ItemType Directory -Path $Staging -Force | Out-Null

# Cleanup on exit
$cleanupStaging = $Staging
trap { if (Test-Path $cleanupStaging) { Remove-Item $cleanupStaging -Recurse -Force -ErrorAction SilentlyContinue } }

Expand-Archive -Path $BundleFile -DestinationPath $Staging -Force

$Bundle = Join-Path $Staging 'quant-trading-bundle'
if (-not (Test-Path $Bundle)) {
    Write-Host "❌ 包格式不正确，缺少 quant-trading-bundle 目录"
    exit 1
}

# 显示 manifest
$ManifestFile = Join-Path $Bundle 'MANIFEST.json'
if (Test-Path $ManifestFile) {
    Write-Host "`u{1F4E6} Quant Trading Import"
    Write-Host "───────────────────────"
    $m = Get-Content $ManifestFile -Raw | ConvertFrom-Json
    Write-Host "导出时间: $($m.exportDate)"
    Write-Host "导出主机: $($m.exportHost)"
    Write-Host "导出用户: $($m.exportUser)"
    Write-Host '包含:'
    $m.contents.PSObject.Properties | ForEach-Object {
        Write-Host "  $($_.Name): $($_.Value)"
    }
    Write-Host "───────────────────────"
}

$DataDir = if ($env:QUANT_DATA_DIR) { $env:QUANT_DATA_DIR } else { Join-Path $HOME '.openclaw\quant-trading' }

# 确定要恢复哪些部分
$RestoreConfig = $true
$RestoreData = $true
$RestorePython = $true
$RestoreMeta = $true

if ($ConfigOnly) {
    $RestoreData = $false; $RestorePython = $false; $RestoreMeta = $false
} elseif ($DataOnly) {
    $RestoreConfig = $false; $RestorePython = $false; $RestoreMeta = $false
} elseif ($PythonOnly) {
    $RestoreConfig = $false; $RestoreData = $false; $RestoreMeta = $false
}

# ── 确认 ─────────────────────────────────────────────────────────────────────
if (-not $Force -and -not $DryRun) {
    Write-Host ""
    Write-Host "将要恢复:"
    if ($RestoreConfig) { Write-Host "  • 配置文件 → config-custom\openclaw.json" }
    if ($RestoreData)   { Write-Host "  • 运行时数据 → $DataDir" }
    if ($RestoreData)   { Write-Host "  • 缓存数据库 → ~/data/cache/" }
    if ($RestoreData)   { Write-Host "  • ~/.openclaw 目录 (workspace, identity, cron, feishu)" }
    if ($RestorePython) { Write-Host "  • Python 策略 → quant-trading\python\" }
    if ($RestoreMeta)   { Write-Host "  • 插件元信息 → quant-trading\" }
    Write-Host ""
    $answer = Read-Host "确认导入? (y/N)"
    if ($answer -ne 'y' -and $answer -ne 'Y') {
        Write-Host "取消导入"
        exit 0
    }
}

function Backup-File {
    param([string]$Target)
    if ($NoBackup -or $DryRun) { return }
    if (Test-Path $Target) {
        $bak = "${Target}.bak-${DateTag}"
        Copy-Item $Target $bak -Force
        Write-Host "  `u{1F4C2} 已备份: $(Split-Path -Leaf $Target) → $(Split-Path -Leaf $bak)"
    }
}

# ── 1. 恢复配置 ──────────────────────────────────────────────────────────────
$ConfigBundleDir = Join-Path $Bundle 'config'
if ($RestoreConfig -and (Test-Path $ConfigBundleDir)) {
    Write-Host ""
    Write-Host "🔧 恢复配置..."

    $srcConfig = Join-Path $ConfigBundleDir 'openclaw.json'
    if (Test-Path $srcConfig) {
        $targetConfig = Join-Path $RepoRoot 'config-custom\openclaw.json'
        if ($DryRun) {
            Write-Host "  [dry-run] 会合并配置到 $targetConfig"
        } else {
            $configDir = Split-Path -Parent $targetConfig
            if (-not (Test-Path $configDir)) {
                New-Item -ItemType Directory -Path $configDir -Force | Out-Null
            }
            Backup-File $targetConfig
            Copy-Item $srcConfig $targetConfig -Force
            Write-Host "  ✓ 配置文件已恢复"
        }
    }

    $srcHomeConfig = Join-Path $ConfigBundleDir 'openclaw-home.json'
    if (Test-Path $srcHomeConfig) {
        $homeConfig = Join-Path $HOME '.openclaw\openclaw.json'
        if ($DryRun) {
            Write-Host "  [dry-run] 会复制到 $homeConfig"
        } else {
            $ocDir = Join-Path $HOME '.openclaw'
            if (-not (Test-Path $ocDir)) {
                New-Item -ItemType Directory -Path $ocDir -Force | Out-Null
            }
            Backup-File $homeConfig
            Copy-Item $srcHomeConfig $homeConfig -Force
            Write-Host "  ✓ ~/.openclaw/openclaw.json"
        }
    }
}

# ── 2. 恢复运行时数据 ────────────────────────────────────────────────────────
$DataBundleDir = Join-Path $Bundle 'data'
if ($RestoreData -and (Test-Path $DataBundleDir) -and (Get-ChildItem $DataBundleDir -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "📊 恢复运行时数据..."

    if ($DryRun) {
        Write-Host "  [dry-run] 会复制到 $DataDir"
        Get-ChildItem $DataBundleDir -Recurse -File | ForEach-Object { Write-Host "  [dry-run]  $($_.FullName)" }
    } else {
        if (-not (Test-Path $DataDir)) {
            New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        }
        # Copy files
        Get-ChildItem $DataBundleDir -File | ForEach-Object {
            $target = Join-Path $DataDir $_.Name
            Backup-File $target
            Copy-Item $_.FullName $target -Force
            Write-Host "  ✓ $($_.Name)"
        }
        # Copy subdirectories
        Get-ChildItem $DataBundleDir -Directory | ForEach-Object {
            $targetSubDir = Join-Path $DataDir $_.Name
            if (-not (Test-Path $targetSubDir)) {
                New-Item -ItemType Directory -Path $targetSubDir -Force | Out-Null
            }
            Copy-Item "$($_.FullName)\*" $targetSubDir -Recurse -Force
            Write-Host "  ✓ $($_.Name)\"
        }
    }
}

# ── 3. 恢复缓存数据库 ─────────────────────────────────────────────────────────
$CacheBundleDir = Join-Path $Bundle 'cache'
$CacheTarget = Join-Path $HOME '.openclaw\extensions\quant-trading\cache'
if ($RestoreData -and (Test-Path $CacheBundleDir) -and (Get-ChildItem $CacheBundleDir -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "💾 恢复缓存数据库..."

    if ($DryRun) {
        Write-Host "  [dry-run] 会复制到 $CacheTarget"
        Get-ChildItem $CacheBundleDir -File | ForEach-Object { Write-Host "  [dry-run]  $($_.Name)" }
    } else {
        if (-not (Test-Path $CacheTarget)) {
            New-Item -ItemType Directory -Path $CacheTarget -Force | Out-Null
        }
        Get-ChildItem $CacheBundleDir -File | ForEach-Object {
            $target = Join-Path $CacheTarget $_.Name
            Backup-File $target
            Copy-Item $_.FullName $target -Force
            $fsize = '{0:N2} MB' -f ($_.Length / 1MB)
            Write-Host "  ✓ $($_.Name) ($fsize)"
        }
    }
}

# ── 3.5 恢复 ~/.openclaw 其他目录 ────────────────────────────────────────────
$HomeBundleDir = Join-Path $Bundle 'openclaw-home'
$OpenclawHome = Join-Path $HOME '.openclaw'
if ($RestoreData -and (Test-Path $HomeBundleDir) -and (Get-ChildItem $HomeBundleDir -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "🏠 恢复 ~/.openclaw 数据..."

    # workspace/
    $wsBundle = Join-Path $HomeBundleDir 'workspace'
    if (Test-Path $wsBundle) {
        $wsDest = Join-Path $OpenclawHome 'workspace'
        if ($DryRun) {
            Write-Host "  [dry-run] workspace/ → $wsDest"
        } else {
            if (-not (Test-Path $wsDest)) {
                New-Item -ItemType Directory -Path $wsDest -Force | Out-Null
            }
            if (-not $NoBackup) {
                Get-ChildItem $wsDest -Filter '*.md' -File -ErrorAction SilentlyContinue | ForEach-Object {
                    Backup-File $_.FullName
                }
            }
            Copy-Item "$wsBundle\*" $wsDest -Recurse -Force -Exclude '.DS_Store'
            $wsCount = (Get-ChildItem $wsDest -Filter '*.md' -File -Recurse).Count
            Write-Host "  ✓ workspace/ ($wsCount 个文件)"
        }
    }

    # identity/
    $idBundle = Join-Path $HomeBundleDir 'identity'
    if (Test-Path $idBundle) {
        $idDest = Join-Path $OpenclawHome 'identity'
        if ($DryRun) {
            Write-Host "  [dry-run] identity/ → $idDest"
        } else {
            if (-not (Test-Path $idDest)) {
                New-Item -ItemType Directory -Path $idDest -Force | Out-Null
            }
            Backup-File (Join-Path $idDest 'device.json')
            Copy-Item "$idBundle\*" $idDest -Recurse -Force
            Write-Host "  ✓ identity/ (设备密钥)"
        }
    }

    # cron/
    $cronBundle = Join-Path $HomeBundleDir 'cron'
    if (Test-Path $cronBundle) {
        $cronDest = Join-Path $OpenclawHome 'cron'
        if ($DryRun) {
            Write-Host "  [dry-run] cron/ → $cronDest"
        } else {
            if (-not (Test-Path $cronDest)) {
                New-Item -ItemType Directory -Path $cronDest -Force | Out-Null
            }
            Backup-File (Join-Path $cronDest 'jobs.json')
            Copy-Item "$cronBundle\*" $cronDest -Recurse -Force
            Write-Host "  ✓ cron/ (定时任务)"
        }
    }

    # feishu/
    $feishuBundle = Join-Path $HomeBundleDir 'feishu'
    if (Test-Path $feishuBundle) {
        $feishuDest = Join-Path $OpenclawHome 'feishu'
        if ($DryRun) {
            Write-Host "  [dry-run] feishu/ → $feishuDest"
        } else {
            if (-not (Test-Path $feishuDest)) {
                New-Item -ItemType Directory -Path $feishuDest -Force | Out-Null
            }
            Copy-Item "$feishuBundle\*" $feishuDest -Recurse -Force -Exclude '.DS_Store'
            Write-Host "  ✓ feishu/ (去重状态)"
        }
    }
}

# ── 4. 恢复 Python 策略 ──────────────────────────────────────────────────────
$PyBundleDir = Join-Path $Bundle 'python'
$PythonTarget = Join-Path $ExtDir 'python'
if ($RestorePython -and (Test-Path $PyBundleDir) -and (Get-ChildItem $PyBundleDir -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "🐍 恢复 Python 策略..."

    if ($DryRun) {
        Write-Host "  [dry-run] 会同步到 $PythonTarget"
        Get-ChildItem $PyBundleDir -Filter '*.py' -Recurse | ForEach-Object { Write-Host "  [dry-run]  $($_.FullName)" }
    } else {
        if ((Test-Path $PythonTarget) -and -not $NoBackup) {
            $pyBak = "${PythonTarget}.bak-${DateTag}"
            Copy-Item $PythonTarget $pyBak -Recurse -Force
            Write-Host "  `u{1F4C2} 已备份: python\ → python.bak-${DateTag}\"
        }
        if (-not (Test-Path $PythonTarget)) {
            New-Item -ItemType Directory -Path $PythonTarget -Force | Out-Null
        }
        Get-ChildItem $PyBundleDir -Recurse |
            Where-Object { $_.FullName -notmatch '__pycache__' -and $_.Extension -ne '.pyc' } |
            ForEach-Object {
                $relPath = $_.FullName.Substring($PyBundleDir.Length)
                $targetPath = Join-Path $PythonTarget $relPath
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
        $pyCount = (Get-ChildItem $PythonTarget -Filter '*.py' -Recurse).Count
        Write-Host "  ✓ 已恢复 $pyCount 个 .py 文件"
    }
}

# ── 5. 恢复元信息 + TypeScript 源码 ──────────────────────────────────────────
$MetaBundleDir = Join-Path $Bundle 'meta'
if ($RestoreMeta -and (Test-Path $MetaBundleDir) -and (Get-ChildItem $MetaBundleDir -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "📋 恢复插件元信息..."

    if ($DryRun) {
        Write-Host "  [dry-run] 会复制到 $ExtDir"
    } else {
        foreach ($f in 'openclaw.plugin.json', 'package.json', 'README.md', 'index.ts') {
            $src = Join-Path $MetaBundleDir $f
            if (Test-Path $src) {
                $target = Join-Path $ExtDir $f
                Backup-File $target
                Copy-Item $src $target -Force
                Write-Host "  ✓ $f"
            }
        }

        $srcDir = Join-Path $MetaBundleDir 'src'
        if (Test-Path $srcDir) {
            $srcTarget = Join-Path $ExtDir 'src'
            if ((Test-Path $srcTarget) -and -not $NoBackup) {
                $srcBak = "${srcTarget}.bak-${DateTag}"
                Copy-Item $srcTarget $srcBak -Recurse -Force
                Write-Host "  `u{1F4C2} 已备份: src\ → src.bak-${DateTag}\"
            }
            if (-not (Test-Path $srcTarget)) {
                New-Item -ItemType Directory -Path $srcTarget -Force | Out-Null
            }
            Copy-Item "$srcDir\*" $srcTarget -Recurse -Force
            Write-Host "  ✓ TypeScript 源码"
        }
    }
}

# ── 完成 ─────────────────────────────────────────────────────────────────────
Write-Host ""
if ($DryRun) {
    Write-Host "🔍 Dry-run 完成，未做任何更改"
} else {
    Write-Host "✅ 导入完成！"
    Write-Host ""
    Write-Host "后续步骤:"
    Write-Host "  1. 检查 config-custom\openclaw.json 中的配置（API密钥等可能需要更新）"
    Write-Host "  2. cd quant-trading\python; pip install -r requirements.txt"
    Write-Host "  3. pnpm install  (安装 Node 依赖)"
    Write-Host "  4. pnpm build    (重新构建)"
}

# Cleanup staging
Remove-Item $Staging -Recurse -Force -ErrorAction SilentlyContinue
