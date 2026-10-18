param(
    [Parameter(Mandatory=$true)][string]$Deployment,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$LogRoot,
    [string]$CpuAffinity = ''
)
$ErrorActionPreference = 'Stop'
$paperRunner = Join-Path $PSScriptRoot 'run-research-paper.ps1'
foreach ($paperPath in @($Deployment, $Python, $paperRunner)) {
    if (-not (Test-Path -LiteralPath $paperPath -PathType Leaf)) { throw 'Required Paper file is missing' }
    if ($paperPath.Contains('"')) { throw 'Quoted file paths are not supported' }
}
if ($LogRoot.Contains('"') -or ($CpuAffinity -and $CpuAffinity -notmatch '^\d+(,\d+)*$')) {
    throw 'Invalid Paper log path or CPU affinity'
}
$paperName = 'QT-Research-Paper-CSI500'
$paperExisting = Get-ScheduledTask -TaskName $paperName -ErrorAction SilentlyContinue
if ($paperExisting -and ($paperExisting.Actions.Count -ne 1 -or -not $paperExisting.Actions[0].Arguments.Contains($paperRunner))) {
    throw 'Task name is already used by a different action'
}
$paperArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $paperRunner +
    '" -Deployment "' + $Deployment + '" -Python "' + $Python + '" -LogRoot "' + $LogRoot + '"'
if ($CpuAffinity) { $paperArgs += ' -CpuAffinity "' + $CpuAffinity + '"' }
$paperAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $paperArgs
$paperTriggers = @('18:30','19:30','20:30') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
$paperUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$paperPrincipal = New-ScheduledTaskPrincipal -UserId $paperUser -LogonType Interactive -RunLevel Limited
$paperSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $paperName -Action $paperAction -Trigger $paperTriggers `
    -Principal $paperPrincipal -Settings $paperSettings -Force | Out-Null
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
Export-ScheduledTask -TaskName $paperName | Set-Content -LiteralPath (Join-Path $LogRoot 'scheduled_task.xml') -Encoding Unicode
Get-ScheduledTaskInfo -TaskName $paperName | Select-Object LastRunTime, LastTaskResult, NextRunTime
