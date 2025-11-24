param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [switch]$RemoveRepo,
    [switch]$BackupEnv,
    [switch]$RemoveEnv
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
    Write-Warning "Katalog $InstallDir nie istnieje - nie ma czego usuwac."
    return
}

$InstallDir = (Resolve-Path -LiteralPath $InstallDir | Select-Object -First 1).Path
$InstallDir = [string]$InstallDir
Set-Location $InstallDir

$envPath = Join-Path $InstallDir ".env"
if ($BackupEnv -and (Test-Path $envPath)) {
    $envBackup = "$envPath.bak"
    Write-Host "Kopia zapasowa .env -> $envBackup"
    Copy-Item -Force -Path $envPath -Destination $envBackup
}

$pythonExe = Join-Path $InstallDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path $pythonExe)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonExe = "py"
    } else {
        $pythonExe = $null
    }
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -eq 'Running') {
        Write-Host "Zatrzymywanie uslugi $ServiceName"
        Stop-Service -Name $ServiceName -Force -ErrorAction Stop
        Start-Sleep -Seconds 2
    }
    Write-Host "Usuwanie uslugi $ServiceName"
    if ($pythonExe) {
        & $pythonExe collector_service.py remove
    } else {
        Write-Warning "Brak interpreteru Pythona - usun usluge recznie poleceniem sc delete $ServiceName."
    }
} else {
    Write-Host "Usluga $ServiceName nie jest zarejestrowana."
}

$pathsToRemove = @(
    "$InstallDir\\.venv",
    "$InstallDir\\logs",
    "$InstallDir\\collector_service_config.json"
)

foreach ($item in $pathsToRemove) {
    if (Test-Path $item) {
        Write-Host "Usuwanie $item"
        Remove-Item -Recurse -Force -Path $item
    }
}

if ($RemoveEnv -and (Test-Path $envPath)) {
    Write-Host "Usuwanie $envPath"
    Remove-Item -Force -Path $envPath
}

if ($RemoveRepo) {
    Write-Host "Usuwanie calego katalogu $InstallDir"
    Set-Location -Path ([System.IO.Path]::GetPathRoot($InstallDir))
    Remove-Item -Recurse -Force -LiteralPath $InstallDir
    Write-Host "Repozytorium zostalo usuniete."
} else {
    Write-Host "Podstawowe artefakty zostaly usuniete. Jesli chcesz usunac cale repo, uruchom ponownie z parametrem -RemoveRepo."
}
