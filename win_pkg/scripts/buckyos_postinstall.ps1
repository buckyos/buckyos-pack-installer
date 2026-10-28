$ErrorActionPreference = "Stop"

$HookDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsDir = Split-Path -Parent $HookDir
$Root = Split-Path -Parent $ScriptsDir

$startupConfigSource = Join-Path $Root 'etc/start_config.json'
$startupSecurityRoot = if ($env:BUCKYOS_SECURITY_ROOT) { $env:BUCKYOS_SECURITY_ROOT } else { Join-Path $Root 'security' }
$startupConfigTarget = Join-Path $startupSecurityRoot 'start_config.json'
$startupSourceItem = Get-Item -LiteralPath $startupConfigSource -Force -ErrorAction SilentlyContinue
if ($startupSourceItem) {
    foreach ($startupPath in @($startupConfigSource, $startupSecurityRoot, $startupConfigTarget)) {
        $startupItem = Get-Item -LiteralPath $startupPath -Force -ErrorAction SilentlyContinue
        if ($startupItem -and ($startupItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Cannot move startup config through a link or junction: $startupPath"
        }
    }
    if ($startupSourceItem.PSIsContainer) { throw "Startup config must be a regular file: $startupConfigSource" }
    [void][IO.Directory]::CreateDirectory($startupSecurityRoot)
    if (Test-Path -LiteralPath $startupConfigTarget) {
        if (-not (Test-Path -LiteralPath $startupConfigTarget -PathType Leaf) -or
            (Get-FileHash -LiteralPath $startupConfigSource -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath $startupConfigTarget -Algorithm SHA256).Hash) {
            throw "Startup config conflict: $startupConfigSource and $startupConfigTarget differ; resolve them before reinstalling"
        }
        Remove-Item -LiteralPath $startupConfigSource -Force
    } else {
        [IO.File]::Move($startupConfigSource, $startupConfigTarget)
    }
    Write-Host "Startup config moved to $startupConfigTarget"
}

$TaskName = "BuckyOSNodeDaemonKeepAlive"
$Loader = Join-Path $ScriptsDir "node_daemon_loader.vbs"
$NodeDaemon = Join-Path $Root "bin\node-daemon\node_daemon.exe"
$RunCommand = 'wscript.exe //B //NoLogo "{0}" "{1}"' -f $Loader, $NodeDaemon

& schtasks.exe /Create /TN $TaskName /SC MINUTE /MO 1 /F /TR $RunCommand
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

& schtasks.exe /Run /TN $TaskName
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $RunKey -Force | Out-Null
Set-ItemProperty -Path $RunKey -Name "BuckyOSDaemon" -Value $RunCommand
