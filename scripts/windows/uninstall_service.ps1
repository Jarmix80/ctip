param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [switch]$RemoveConfig
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

if (-not (Test-Path $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje."
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

$pythonExe = Join-Path $InstallDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path $pythonExe)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonExe = "py"
    } else {
        throw "Nie znaleziono środowiska .venv ani Python Launcher."
    }
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -eq 'Running') {
        Write-Host "Zatrzymywanie usługi $ServiceName"
        Stop-Service -Name $ServiceName -Force -ErrorAction Stop
        Start-Sleep -Seconds 2
    }
    Write-Host "Usuwanie usługi"
    & $pythonExe collector_service.py remove
} else {
    Write-Warning "Usługa $ServiceName nie jest zarejestrowana."
}

if ($RemoveConfig) {
    $configPath = Join-Path $InstallDir "collector_service_config.json"
    if (Test-Path $configPath) {
        Remove-Item -Force $configPath
        Write-Host "Usunięto plik konfiguracyjny: $configPath"
    }
}

Write-Host "Operacja zakończona."
