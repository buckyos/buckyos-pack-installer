param([ValidateSet('Select', 'Apply')][string]$Action, [string]$Root,
      [string]$Profiles, [string]$State, [switch]$Silent, [switch]$Lite)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'install_mode.ps1')
    # NSIS runs helpers without a console. Preserve its native GUI interaction.
    function Read-Host {
        param([string]$Prompt)
        Add-Type -AssemblyName System.Windows.Forms
        $message = $Prompt + "`r`n`r`nLite disables containers and user service apps, supports static web and bundled scripts, and runs at most one Agent.`r`n`r`nTo install Full instead, exit, install and start Docker Desktop yourself, then rerun this installer."
        if ([Windows.Forms.MessageBox]::Show($message, 'BuckyOS', 'YesNo', 'Question', 'Button2') -eq 'Yes') { return 'yes' }
        return 'no'
    }
    if ($Action -eq 'Select') {
        $mode = Select-DesktopMode $Root -Silent:$Silent -Lite:$Lite -Offline
        $profile = Read-DesktopProfile (Join-Path $Profiles ($mode.Edition + '.json'))
        if ($profile.edition -cne $mode.Edition) { throw 'Mismatched product profile.' }
        [IO.File]::WriteAllText($State, (ConvertTo-Json $mode), [Text.UTF8Encoding]::new($false))
    } else {
        $mode = Get-Content -LiteralPath $State -Raw -Encoding UTF8 | ConvertFrom-Json
        Set-DesktopMode $Root $Profiles $mode
        if ($mode.Edition -eq 'lite') {
            # Images are program assets, never user container volumes.
            $images = Join-Path $Root 'bin/paios-images'
            if (Test-Path -LiteralPath $images) { Remove-Item -LiteralPath $images -Recurse -Force }
        }
    }
    exit 0
} catch {
    Write-Host $_.Exception.Message
    if (-not $Silent) {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'BuckyOS installation failed', 'OK', 'Error') | Out-Null
    }
    exit 21
}
