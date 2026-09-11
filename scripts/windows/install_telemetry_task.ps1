param(
    [string]$InstallDir = 'D:\CTIP',
    [switch]$Backfill
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
$script = Join-Path $InstallDir 'scripts\windows\run_telemetry.py'
if (-not (Test-Path $python) -or -not (Test-Path (Join-Path $InstallDir '.env.telemetry'))) {
    throw 'Brak interpretera lub prywatnej konfiguracji telemetrii.'
}
$arguments = '"' + $script + '" --once'
$name = 'CTIP-Telemetry'
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(15) -RepetitionInterval (New-TimeSpan -Minutes 15))
)
if ($Backfill) {
    $name = 'CTIP-Telemetry-Backfill'
    $arguments += ' --backfill'
    $triggers = @((New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)))
}
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $InstallDir
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
Write-Output ('Zarejestrowano zadanie ' + $name)
