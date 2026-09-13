param([string]$InstallDir = 'D:\CTIP')

$ErrorActionPreference = 'Stop'
$python = Join-Path $InstallDir '.venv\Scripts\python.exe'
$script = Join-Path $InstallDir 'scripts\windows\run_orbit.py'
if (-not (Test-Path $python) -or -not (Test-Path $script) -or -not (Test-Path (Join-Path $InstallDir '.env'))) {
    throw 'Brak interpretera, skryptu lub prywatnej konfiguracji ORBIT.'
}
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + $script + '" --loop --project-only') -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 6 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'CTIP-ORBIT' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'Zarejestrowano CTIP-ORBIT. Start wymaga aktywnej flagi oraz osobnego odbioru produkcyjnego.'
