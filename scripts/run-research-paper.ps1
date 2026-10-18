param(
    [Parameter(Mandatory=$true)][string]$Deployment,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$LogRoot,
    [string]$CpuAffinity = ''
)
$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$paperStarted = [DateTimeOffset]::UtcNow
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$paperLog = Join-Path $LogRoot ('run-' + $paperStarted.ToString('yyyyMMddTHHmmssfffZ') + '.log')
$paperScript = Join-Path $PSScriptRoot '../quant-trading/python/scripts/research_paper_daily.py'
$paperArguments = @('-X', 'faulthandler', $paperScript, '--deployment', $Deployment, '--execute')
if ($CpuAffinity) { $paperArguments += @('--cpu-affinity', $CpuAffinity) }
$paperExit = 1
try {
    Get-Command $Python -CommandType Application -ErrorAction Stop | Out-Null
    # PS 5.1 treats a native stderr line as NativeCommandError under Stop.
    # Capture stderr while retaining the actual process exit code.
    $ErrorActionPreference = 'Continue'
    & $Python @paperArguments *> $paperLog
    $paperExit = $LASTEXITCODE
} catch {
    $_.Exception.GetType().FullName | Out-File -LiteralPath $paperLog -Append -Encoding utf8
} finally {
    $ErrorActionPreference = 'Stop'
}
$paperResult = [ordered]@{
    started_at = $paperStarted.ToString('o')
    finished_at = [DateTimeOffset]::UtcNow.ToString('o')
    exit_code = $paperExit
    log_path = $paperLog
    cpu_affinity = $CpuAffinity
    broker_contacted = $false
}
$paperTemp = Join-Path $LogRoot ('result-' + [guid]::NewGuid().ToString('N') + '.tmp')
$paperResult | ConvertTo-Json | Set-Content -LiteralPath $paperTemp -Encoding utf8
Move-Item -LiteralPath $paperTemp -Destination (Join-Path $LogRoot 'latest_process_result.json') -Force
exit $paperExit
