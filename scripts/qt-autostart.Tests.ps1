$scriptPath = Join-Path $PSScriptRoot 'qt-autostart.ps1'
$parseErrors = $null
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ($parseErrors | Out-String) }
foreach ($name in @('Get-QTServiceProcess', 'Stop-QTService')) {
    $functionAst = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $false)
    . ([scriptblock]::Create($functionAst.Extent.Text))
}

Describe 'QT standalone service lifecycle' {
    BeforeEach {
        $script:entry = 'E:\work\QT\quant-trading\standalone.ts'
        $script:listeners = @(101)
        $script:processes = @{
            101 = [pscustomobject]@{
                ProcessId = 101; ParentProcessId = 100; Name = 'node.exe'
                CommandLine = 'node.exe --import loader.mjs "E:\work\QT\quant-trading\standalone.ts"'
                CreationDate = [datetime]'2026-10-19T10:00:01'
            }
            100 = [pscustomobject]@{
                ProcessId = 100; ParentProcessId = 99; Name = 'node.exe'
                CommandLine = 'node.exe tsx/cli.mjs E:\work\QT\quant-trading\standalone.ts'
                CreationDate = [datetime]'2026-10-19T10:00:00'
            }
            99 = [pscustomobject]@{
                ProcessId = 99; ParentProcessId = 1; Name = 'powershell.exe'
                CommandLine = 'powershell.exe'; CreationDate = [datetime]'2026-10-19T09:59:00'
            }
        }
        Mock Get-NetTCPConnection {
            foreach ($listenerId in $script:listeners) { [pscustomobject]@{ OwningProcess = $listenerId } }
        }
        Mock Get-CimInstance {
            $testProcessId = [int]($Filter -replace '^ProcessId = ', '')
            $script:processes[$testProcessId]
        }
        Mock taskkill.exe {
            $global:LASTEXITCODE = 0
            $script:listeners = @()
        }
        Mock Get-Process { $script:processes[$Id] }
    }

    It 'finds the tsx wrapper without selecting the user shell' {
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        $result.Count | Should Be 1
        $result[0].ProcessId | Should Be 100
    }

    It 'accepts slash-separated absolute entrypoints' {
        $script:processes[101].CommandLine = 'node.exe E:/work/QT/quant-trading/standalone.ts'
        @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry).Count | Should Be 1
    }

    It 'refuses another project on the service port' {
        $script:processes[101].CommandLine = 'node.exe E:\other\quant-trading\standalone.ts'
        { Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry } | Should Throw
        Assert-MockCalled taskkill.exe -Times 0 -Exactly -Scope It
    }

    It 'refuses a different executable even with a matching argument' {
        $script:processes[101].Name = 'python.exe'
        { Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry } | Should Throw
    }

    It 'does not confuse a similarly named script with the entrypoint' {
        $script:processes[101].CommandLine = 'node.exe E:\work\QT\quant-trading\standalone.ts.backup'
        { Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry } | Should Throw
    }

    It 'does not follow a parent PID reused after the service started' {
        $script:processes[100].CreationDate = [datetime]'2026-10-19T10:01:00'
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        $result[0].ProcessId | Should Be 101
    }

    It 'stops the validated tree once and verifies the port is released' {
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        Stop-QTService -ServicePort 18891 -ServiceProcesses $result
        Assert-MockCalled taskkill.exe -Times 1 -Exactly -Scope It
        Assert-MockCalled Get-NetTCPConnection -Times 2 -Exactly -Scope It
    }

    It 'does nothing when already stopped' {
        $script:listeners = @()
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        Stop-QTService -ServicePort 18891 -ServiceProcesses $result
        Assert-MockCalled taskkill.exe -Times 0 -Exactly -Scope It
    }

    It 'refuses to stop a PID whose identity changed after discovery' {
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        $script:processes[100] = [pscustomobject]@{
            ProcessId = 100; Name = 'node.exe'; CommandLine = 'node.exe other.js'
            CreationDate = [datetime]'2026-10-19T10:02:00'
        }
        { Stop-QTService -ServicePort 18891 -ServiceProcesses $result } | Should Throw
        Assert-MockCalled taskkill.exe -Times 0 -Exactly -Scope It
    }

    It 'reports a termination failure instead of proceeding to restart' {
        Mock taskkill.exe { $global:LASTEXITCODE = 1 }
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        { Stop-QTService -ServicePort 18891 -ServiceProcesses $result } | Should Throw
    }

    It 'fails if the port remains occupied after stopping the process' {
        Mock taskkill.exe { $global:LASTEXITCODE = 0 }
        $script:clockTick = 0
        Mock Get-Date {
            $script:clockTick++
            ([datetime]'2026-10-19T10:00:00').AddSeconds(11 * $script:clockTick)
        }
        Mock Start-Sleep {}
        $result = @(Get-QTServiceProcess -ServicePort 18891 -EntryPoint $script:entry)
        { Stop-QTService -ServicePort 18891 -ServiceProcesses $result } | Should Throw
    }
}
