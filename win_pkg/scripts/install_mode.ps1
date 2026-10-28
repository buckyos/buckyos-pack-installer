# Shared by the online installer and the offline NSIS installer. PowerShell 5.1.
function Read-DesktopProfile {
    param([string]$Path)
    if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Product profile must not be a link.' }
    if ((Get-Item -LiteralPath $Path).Length -gt 1048576) { throw 'Oversized product profile.' }
    $p = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (($p.schema_version -isnot [int] -and $p.schema_version -isnot [long]) -or $p.schema_version -ne 1 -or $p.edition -cnotin 'full', 'lite' -or
        $p.capabilities.container -isnot [bool] -or $p.capabilities.user_service_apps -isnot [bool] -or
        $p.capabilities.static_web_apps -isnot [bool]) { throw 'Invalid product profile.' }
    if ($p.edition -ceq 'lite' -and ($p.capabilities.container -or $p.capabilities.user_service_apps -or
        $p.capabilities.max_running_agents -ne 1)) { throw 'Invalid Lite capabilities.' }
    if ($p.edition -ceq 'full' -and (-not $p.capabilities.container -or -not $p.capabilities.user_service_apps -or
        -not $p.capabilities.static_web_apps -or $null -ne $p.capabilities.max_running_agents)) { throw 'Invalid Full capabilities.' }
    return $p
}

function Get-DesktopInstalledEdition {
    param([string]$Root)
    $file = Join-Path $Root 'etc/product.json'
    try { $item = Get-Item -LiteralPath $file -Force -ErrorAction Stop }
    catch [Management.Automation.ItemNotFoundException] { $item = $null }
    if ($item) { return (Read-DesktopProfile $file).edition }
    if ((Test-Path -LiteralPath (Join-Path $Root 'bin/node-daemon/node_daemon.exe')) -or
        ((Test-Path -LiteralPath (Join-Path $Root 'etc/node_identity.json')) -and
        -not (Test-Path -LiteralPath (Join-Path $Root '.online-uninstalled.json'))) -or
        (Test-Path -LiteralPath (Join-Path $Root '.online-installed-version'))) { return 'full' }
    return ''
}

function Invoke-DesktopCommand {
    param([string]$File, [string[]]$Arguments, [int]$Timeout = 20)
    $p = New-Object Diagnostics.Process
    try {
        # Arguments here are fixed command switches, never user input.
        $p.StartInfo.FileName = $File
        $p.StartInfo.Arguments = $Arguments -join ' '
        $p.StartInfo.UseShellExecute = $false
        $p.StartInfo.CreateNoWindow = $true
        $p.StartInfo.RedirectStandardOutput = $true
        $p.StartInfo.RedirectStandardError = $true
        $p.StartInfo.EnvironmentVariables['WSL_UTF8'] = '1'
        if (-not $p.Start()) { throw "Cannot start $File" }
        $stdout = $p.StandardOutput.ReadToEndAsync()
        $stderr = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit($Timeout * 1000)) { $p.Kill(); $p.WaitForExit(); return @{ Code = -1; Output = ''; Error = 'Command timed out.' } }
        $p.WaitForExit()
        return @{ Code = $p.ExitCode; Output = $stdout.GetAwaiter().GetResult(); Error = $stderr.GetAwaiter().GetResult() }
    } finally { $p.Dispose() }
}

function Find-DesktopProgram {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($Name -eq 'wslc.exe') {
        $file = Join-Path $env:ProgramFiles 'WSL/wslc.exe'
        if (Test-Path -LiteralPath $file -PathType Leaf) { return $file }
    }
    return $null
}

function Get-DesktopContainerBackend {
    param([string]$Preferred = '')
    $order = if ($Preferred -eq 'docker') { @('docker', 'wslc') } else { @('wslc', 'docker') }
    foreach ($backend in $order) {
        $file = Find-DesktopProgram ($backend + '.exe')
        if (-not $file) { continue }
        if ($backend -eq 'wslc') {
            $r = Invoke-DesktopCommand $file @('system', 'info')
            # Information alone need not start a VM. Local image enumeration
            # contacts the engine without pulling an image or requiring stdin.
            if ($r.Code -eq 0 -and $r.Output -match '(?im)^\s*Server:') {
                $session = Invoke-DesktopCommand $file @('image', 'ls')
                if ($session.Code -eq 0) { return 'wslc' }
            }
        } else {
            $r = Invoke-DesktopCommand $file @('info', '--format', '{{.OSType}}')
            if ($r.Code -eq 0 -and $r.Output.Trim() -ceq 'linux') { return 'docker' }
        }
    }
    return ''
}

function Get-DesktopWslPreparation {
    if ([int](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).BuildNumber -lt 19041) { throw 'This Windows build does not support automatic WSL setup.' }
    # DefaultVersion is separate from the installed WSL software version.
    $defaultVersion = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -Name DefaultVersion -ErrorAction SilentlyContinue).DefaultVersion
    $wsl1Distros = @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty -LiteralPath $_.PSPath -Name Version -ErrorAction SilentlyContinue).Version -eq 1 })
    if ($null -eq $defaultVersion -and $wsl1Distros.Count) { $defaultVersion = 1 }
    $vmp = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction Stop
    $boot = Invoke-DesktopCommand 'bcdedit.exe' @('/enum', '{current}')
    if ($boot.Code -ne 0) { throw 'Cannot determine the Windows hypervisor boot setting.' }
    if ($boot.Output -match '(?im)hypervisorlaunchtype\s+Off' -or
        (($defaultVersion -eq 1 -or $wsl1Distros.Count) -and $vmp.State -ne 'Enabled')) { throw 'Existing WSL 1 / disabled hypervisor settings are preserved.' }
    $cpu = @(Get-CimInstance Win32_Processor -ErrorAction Stop)
    if (-not (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).HypervisorPresent -and
        @($cpu | Where-Object { -not $_.VirtualizationFirmwareEnabled -or -not $_.SecondLevelAddressTranslationExtensions }).Count) {
        throw 'Hardware virtualization is unavailable.'
    }
    $wsl = Find-DesktopProgram 'wsl.exe'
    $installed = $false
    if ($wsl) { $installed = (Invoke-DesktopCommand $wsl @('--version')).Code -eq 0 }
    return @{ Installed = $installed; Command = $wsl; DefaultVersion = $defaultVersion; Reboot = ($vmp.State -eq 'EnablePending') }
}

function Install-DesktopWsl {
    param($Preparation)
    if ($Preparation.Reboot) { return 'reboot' }
    $file = $Preparation.Command
    if (-not $file) { $file = Join-Path $env:SystemRoot 'System32/wsl.exe' }
    $arguments = if ($Preparation.Installed) { @('--update', '--web-download') } else { @('--install', '--no-distribution', '--web-download', '--no-launch') }
    try { $r = Invoke-DesktopCommand $file $arguments 1800 }
    finally {
        # Even an inbox-to-Store WSL migration must retain the user's default.
        if ($Preparation.DefaultVersion -in 1, 2) {
            Set-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -Name DefaultVersion -Value ([int]$Preparation.DefaultVersion) -Type DWord
        }
    }
    if ($r.Code -in 3010, 1641 -or (Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform).State -eq 'EnablePending') { return 'reboot' }
    if ($r.Code -ne 0) { throw "WSL preparation failed (exit $($r.Code)): $($r.Output) $($r.Error)" }
    return 'ready'
}

function Select-DesktopMode {
    param([string]$Root, [switch]$Lite, [switch]$Silent, [switch]$Stage, [switch]$Offline, [switch]$Resumed)
    $old = Get-DesktopInstalledEdition $Root
    if ($old -eq 'full' -and $Lite) { throw '--lite conflicts with the existing Full installation; mode conversion is not supported.' }
    if ($old -eq 'lite' -or $Lite) { return @{ Edition = 'lite'; Backend = '' } }
    if ($Stage) { return @{ Edition = 'full'; Backend = '' } }
    $preferred = ''
    $backendFile = Join-Path $Root 'etc/container-runtime'
    if (Test-Path -LiteralPath $backendFile) { $preferred = (Get-Content -LiteralPath $backendFile -Raw).Trim() }
    $backend = Get-DesktopContainerBackend $preferred
    if ($backend) { return @{ Edition = 'full'; Backend = $backend } }
    if ($Silent) { $script:InstallerExitCode = 21; throw 'Full requires a usable WSLC or Docker Linux engine. Silent installation does not prepare dependencies or fall back to Lite.' }
    if (-not $Offline -and -not $Resumed) {
        try { $prep = Get-DesktopWslPreparation } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow; $prep = $null }
        if ($prep -and (Read-Host 'Automatically install/update WSL for WSLC? [y/N]') -match '^(?i:y|yes)$') {
            try {
                Write-Host 'Preparing WSL/WSLC. This may take several minutes.'
                if ((Install-DesktopWsl $prep) -eq 'reboot') { return @{ Edition = 'full'; Backend = ''; Reboot = $true } }
                $backend = Get-DesktopContainerBackend
                if ($backend) { return @{ Edition = 'full'; Backend = $backend } }
            } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow }
        }
    }
    Write-Host 'You can exit, install and start Docker Desktop yourself, then run this installer again.' -ForegroundColor Yellow
    if ($old -eq 'full') { throw 'Full container dependencies are unavailable. Existing Full installation is preserved.' }
    Write-Host 'Lite disables containers and user service apps, supports static web and bundled script apps, and runs at most one Agent.'
    if ((Read-Host 'Install Lite? [y/N]') -notmatch '^(?i:y|yes)$') { throw 'Installation cancelled.' }
    return @{ Edition = 'lite'; Backend = '' }
}

function Set-DesktopMode {
    param([string]$Root, [string]$Profiles, $Mode)
    $source = Join-Path $Profiles ($Mode.Edition + '.json')
    $p = Read-DesktopProfile $source
    if ($p.edition -cne $Mode.Edition) { throw 'Mismatched product profile.' }
    New-Item -ItemType Directory -Path (Join-Path $Root 'etc') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $Root 'etc/product.json'), (ConvertTo-Json $p -Depth 32) + "`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $Root 'etc/container-runtime'), [string]$Mode.Backend + "`n", [Text.UTF8Encoding]::new($false))
}
