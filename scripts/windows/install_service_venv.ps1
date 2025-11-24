param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService"
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Uruchom PowerShell w trybie Administratora."
    }
}

Assert-Admin

if (-not (Test-Path -LiteralPath $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje. Sklonuj repozytorium przed uruchomieniem skryptu."
}

$InstallDir = (Resolve-Path -LiteralPath $InstallDir | Select-Object -First 1).Path
Set-Location $InstallDir

$pythonExe = Join-Path $InstallDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Brak interpretera .venv ($pythonExe). Utworz .venv i zainstaluj zaleznosci przed uruchomieniem skryptu."
}

$logDir = Join-Path $InstallDir "logs\\collector"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$config = [ordered]@{
    work_dir   = $InstallDir
    python     = $pythonExe
    script     = Join-Path $InstallDir "collector_full.py"
    log_dir    = $logDir
    stdout_log = Join-Path $logDir "collector_stdout.log"
    stderr_log = Join-Path $logDir "collector_stderr.log"
}

$configPath = Join-Path $InstallDir "collector_service_config.json"
$config | ConvertTo-Json -Depth 2 | Set-Content -Encoding UTF8 -Path $configPath
Write-Host "Zapisano konfiguracje uslugi: $configPath"

$envFile = Join-Path $InstallDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Warning ".env nie zostal znaleziony - uzupelnij parametry PBX/PG/SMS przed startem uslugi."
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
    Write-Host "Usluga $ServiceName juz istnieje - zostanie przeinstalowana."
    try {
        Invoke-ServiceCommand -Command "stop"
    } catch {
        Write-Warning "Nie udalo sie zatrzymac uslugi (mogla juz nie dzialac)."
    }
    Start-Sleep -Seconds 2
    Invoke-ServiceCommand -Command "remove"
}

Invoke-ServiceCommand -Command "install"
Invoke-ServiceCommand -Command "start"

Write-Host "Instalacja zakonczona. Monitoruj logi w $logDir."
