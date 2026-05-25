param(
    [string]$InstallDir = "D:\CTIP",
    [string]$ServicePrefix = "CTIP",
    [string]$NssmPath = "",
    [string]$UvicornHost = "0.0.0.0",
    [int]$UvicornPort = 8000,
    [int]$UvicornWorkers = 1,
    [switch]$InstallPublicForms,
    [string]$PublicFormsHost = "127.0.0.1",
    [int]$PublicFormsPort = 8100,
    [int]$PublicFormsWorkers = 2
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Uruchom PowerShell w trybie Administratora."
    }
}

function Resolve-Nssm {
    param([string]$Candidate)

    if (-not [string]::IsNullOrWhiteSpace($Candidate) -and (Test-Path $Candidate)) {
        return (Resolve-Path $Candidate).Path
    }

    $guess = "C:\Program Files\nssm\nssm.exe"
    if (Test-Path $guess) {
        return (Resolve-Path $guess).Path
    }

    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw "Nie znaleziono nssm.exe. Zainstaluj NSSM (https://nssm.cc/download) i podaj sciezke parametrem -NssmPath."
}

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}

function Install-NssmService {
    param(
        [string]$Nssm,
        [string]$ServiceName,
        [string]$Executable,
        [string[]]$Args,
        [string]$WorkDir,
        [string]$StdoutLog,
        [string]$StderrLog
    )

    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -eq "Running") {
            Write-Host "Zatrzymuje istniejaca usluge $ServiceName"
            & $Nssm stop $ServiceName | Out-Null
            Start-Sleep -Seconds 2
        }
        Write-Host "Usuwam istniejaca usluge $ServiceName"
        & $Nssm remove $ServiceName confirm | Out-Null
    }

    Write-Host "Rejestruje usluge $ServiceName"
    & $Nssm install $ServiceName $Executable @Args | Out-Null
    & $Nssm set $ServiceName AppDirectory $WorkDir | Out-Null
    & $Nssm set $ServiceName AppStdout $StdoutLog | Out-Null
    & $Nssm set $ServiceName AppStderr $StderrLog | Out-Null
    & $Nssm set $ServiceName AppStopMethodConsole 15000 | Out-Null
    & $Nssm set $ServiceName AppStopMethodThreads 15000 | Out-Null
    & $Nssm set $ServiceName AppThrottle 1500 | Out-Null
    & $Nssm set $ServiceName Start SERVICE_AUTO_START | Out-Null
    & $Nssm set $ServiceName AppRotateFiles 1 | Out-Null
    & $Nssm set $ServiceName AppRotateBytes 10485760 | Out-Null
    & $Nssm set $ServiceName AppRotateSeconds 86400 | Out-Null

    Write-Host "Start uslugi $ServiceName"
    & $Nssm start $ServiceName | Out-Null
}

Assert-Admin

if (-not (Test-Path $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje."
}
$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

$nssm = Resolve-Nssm -Candidate $NssmPath
Write-Host "Uzywam nssm: $nssm"

$pythonExe = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Nie znaleziono interpretera $pythonExe. Uruchom najpierw install_service.ps1 aby zbudowac .venv."
}

$envFile = Join-Path $InstallDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning ".env nie znaleziono w $InstallDir. Panel i SMS beda startowac bez konfiguracji."
}

Write-Host "Sprawdzam uvicorn"
$cmdOutput = & $pythonExe -m uvicorn --version 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Brak modulu uvicorn w .venv. Uruchom pip install -e ."
}

$webScript = Join-Path $InstallDir "scripts\windows\run_ctip_web.py"
if (-not (Test-Path $webScript)) {
    throw "Nie znaleziono bootstrapu CTIP-Web: $webScript"
}

if ($UvicornHost -ne "0.0.0.0" -or $UvicornPort -ne 8000 -or $UvicornWorkers -ne 1) {
    Write-Warning "CTIP-Web na Windows startuje przez run_ctip_web.py i wymaga host=0.0.0.0, port=8000, workers=1. Przekazane parametry zostana zignorowane."
}

$logsWeb = Join-Path $InstallDir "logs\web"
$logsSms = Join-Path $InstallDir "logs\sms"
$logsForms = Join-Path $InstallDir "logs\forms_public"
Ensure-Dir -Path $logsWeb
Ensure-Dir -Path $logsSms
Ensure-Dir -Path $logsForms

$webService = "$ServicePrefix-Web"
$smsService = "$ServicePrefix-SMS"
$formsService = "$ServicePrefix-FormsPublic"

$webArgs = @(
    "-u", $webScript
)
$smsArgs = @(
    "-u", (Join-Path $InstallDir "sms_sender.py")
)
$formsArgs = @(
    "-m", "uvicorn", "app.public_forms_app:app",
    "--host", $PublicFormsHost,
    "--port", $PublicFormsPort.ToString(),
    "--workers", $PublicFormsWorkers.ToString()
)

Install-NssmService -Nssm $nssm -ServiceName $webService -Executable $pythonExe -Args $webArgs `
    -WorkDir $InstallDir -StdoutLog (Join-Path $logsWeb "web_stdout.log") -StderrLog (Join-Path $logsWeb "web_stderr.log")

Install-NssmService -Nssm $nssm -ServiceName $smsService -Executable $pythonExe -Args $smsArgs `
    -WorkDir $InstallDir -StdoutLog (Join-Path $logsSms "sms_stdout.log") -StderrLog (Join-Path $logsSms "sms_stderr.log")

if ($InstallPublicForms) {
    Install-NssmService -Nssm $nssm -ServiceName $formsService -Executable $pythonExe -Args $formsArgs `
        -WorkDir $InstallDir -StdoutLog (Join-Path $logsForms "forms_stdout.log") -StderrLog (Join-Path $logsForms "forms_stderr.log")
}

if ($InstallPublicForms) {
    Write-Host "Gotowe. Sprawdz status: Get-Service $webService, $smsService, $formsService"
} else {
    Write-Host "Gotowe. Sprawdz status: Get-Service $webService, $smsService"
}
