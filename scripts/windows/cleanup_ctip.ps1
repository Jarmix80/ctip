param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [switch]$RemoveRepo
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
    Write-Warning "Katalog $InstallDir nie istnieje – nie ma czego usuwać."
    return
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

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
        Write-Host "Zatrzymywanie usługi $ServiceName"
        Stop-Service -Name $ServiceName -Force -ErrorAction Stop
        Start-Sleep -Seconds 2
    }
    Write-Host "Usuwanie usługi $ServiceName"
    if ($pythonExe) {
        & $pythonExe collector_service.py remove
    } else {
        Write-Warning "Brak interpreteru Pythona – usuń usługę ręcznie poleceniem sc delete $ServiceName."
    }
} else {
    Write-Host "Usługa $ServiceName nie jest zarejestrowana."
}

$pathsToRemove = @(
    Join-Path $InstallDir ".venv",
    Join-Path $InstallDir "logs",
    Join-Path $InstallDir "collector_service_config.json"
)

foreach ($item in $pathsToRemove) {
    if (Test-Path $item) {
        Write-Host "Usuwanie $item"
        Remove-Item -Recurse -Force -Path $item
    }
}

if ($RemoveRepo) {
    Write-Host "Usuwanie całego katalogu $InstallDir"
    Set-Location -Path ([System.IO.Path]::GetPathRoot($InstallDir))
    Remove-Item -Recurse -Force -Path $InstallDir
    Write-Host "Repozytorium zostało usunięte."
} else {
    Write-Host "Podstawowe artefakty zostały usunięte. Jeśli chcesz usunąć całe repo, uruchom ponownie z parametrem -RemoveRepo."
}
