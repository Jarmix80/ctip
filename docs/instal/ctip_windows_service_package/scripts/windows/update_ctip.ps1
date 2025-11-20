param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [string]$GitRemote = "origin",
    [string]$GitBranch = "main",
    [switch]$SkipPip
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

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git nie jest zainstalowany lub nie znajduje sie w PATH."
}

$venvPython = Join-Path $InstallDir ".venv\\Scripts\\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Nie znaleziono srodowiska .venv ($venvPython). Uruchom najpierw install_service.ps1."
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$serviceWasRunning = $false
if ($service -and $service.Status -eq 'Running') {
    Write-Host "Zatrzymywanie uslugi $ServiceName"
    Stop-Service -Name $ServiceName -Force -ErrorAction Stop
    $serviceWasRunning = $true
    Start-Sleep -Seconds 2
}

try {
    Write-Host "git fetch $GitRemote --tags"
    git fetch $GitRemote --tags
    Write-Host "git pull $GitRemote $GitBranch --ff-only"
    git pull $GitRemote $GitBranch --ff-only

    if (-not $SkipPip) {
        Write-Host "Aktualizacja zaleznosci w .venv"
        & $venvPython -m pip install -r requirements.txt
    }
}
finally {
    if ($service -and $serviceWasRunning) {
        try {
            Write-Host "Ponowne uruchamianie uslugi"
            & $venvPython collector_service.py start
        } catch {
            Write-Warning "Nie udalo sie uruchomic uslugi przez collector_service.py start. Sprawdz dzienniki."
        }
    }
}
