param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Uruchom PowerShell w trybie Administratora."
    }
}

function Ensure-Venv {
    param(
        [string]$Launcher,
        [string]$Version,
        [string]$TargetDir
    )

    if (Test-Path $TargetDir) {
        return
    }

    if (-not (Get-Command $Launcher -ErrorAction SilentlyContinue)) {
        throw "Nie znaleziono polecenia $Launcher. Zainstaluj Python Launcher (py.exe) lub ustaw parametr -PythonLauncher."
    }

    Write-Host "Tworzenie środowiska .venv w $TargetDir"
    $args = @()
    if ($Launcher -eq "py" -and -not [string]::IsNullOrWhiteSpace($Version)) {
        $args += "-$Version"
    }
    $args += @("-m", "venv", $TargetDir)
    & $Launcher @args
}

Assert-Admin

if (-not (Test-Path $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje. Sklonuj repozytorium przed uruchomieniem skryptu."
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

$venvPath = Join-Path $InstallDir ".venv"
Ensure-Venv -Launcher $PythonLauncher -Version $PythonVersion -TargetDir $venvPath

$pythonExe = Join-Path $venvPath "Scripts\\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Nie udało się odnaleźć $pythonExe. Sprawdź poprawność instalacji środowiska wirtualnego."
}

Write-Host "Aktualizacja pip i instalacja zależności"
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements.txt

$logDir = Join-Path $InstallDir "logs\\collector"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$config = [ordered]@{
    work_dir  = $InstallDir
    python    = $pythonExe
    script    = Join-Path $InstallDir "collector_full.py"
    log_dir   = $logDir
    stdout_log = Join-Path $logDir "collector_stdout.log"
    stderr_log = Join-Path $logDir "collector_stderr.log"
}

$configPath = Join-Path $InstallDir "collector_service_config.json"
$config | ConvertTo-Json -Depth 2 | Set-Content -Encoding UTF8 -Path $configPath
Write-Host "Zapisano konfigurację usługi: $configPath"

$envFile = Join-Path $InstallDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning ".env nie został znaleziony – uzupełnij parametry PBX/PG/SMS przed startem usługi."
}

function Invoke-ServiceCommand {
    param(
        [string]$Command
    )

    Write-Host "collector_service.py $Command"
    & $pythonExe collector_service.py $Command
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    Write-Host "Usługa $ServiceName już istnieje – zostanie przeinstalowana."
    try {
        Invoke-ServiceCommand -Command "stop"
    } catch {
        Write-Warning "Nie udało się zatrzymać usługi (mogła już nie działać)."
    }
    Start-Sleep -Seconds 2
    Invoke-ServiceCommand -Command "remove"
}

Invoke-ServiceCommand -Command "install"
Invoke-ServiceCommand -Command "start"

Write-Host "Instalacja zakończona. Monitoruj logi w $logDir."
