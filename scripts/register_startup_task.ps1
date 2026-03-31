param(
    [string]$TaskName = "ClassInformationBotQuickTunnel",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$WebPort = 8000,
    [switch]$SyncUrlToEnv,
    [switch]$RestartComposeOnUrlChange
)

$ErrorActionPreference = "Stop"

$workerPath = Join-Path $RepoRoot "scripts\quick_tunnel_worker.ps1"
if (-not (Test-Path $workerPath)) {
    throw "Worker script not found: $workerPath"
}

$argList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $workerPath + '"'),
    "-RepoRoot", ('"' + $RepoRoot + '"'),
    "-WebPort", $WebPort
)

if ($SyncUrlToEnv) { $argList += "-SyncUrlToEnv" }
if ($RestartComposeOnUrlChange) { $argList += "-RestartComposeOnUrlChange" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($argList -join " ")
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Scheduled task registered: $TaskName"
Write-Host "Start manually once: Start-ScheduledTask -TaskName '$TaskName'"
