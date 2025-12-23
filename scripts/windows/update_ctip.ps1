param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [string[]]$ServiceNames = @(),
    [string]$GitRemote = "origin",
    [string]$GitBranch = "main",
    [switch]$SkipPip,
    [switch]$SkipPreCommit,
    [switch]$SkipTests,
    [switch]$ForceStartOnFailure
)

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Uruchom PowerShell w trybie Administratora."
    }
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) {
            return
        }
        if ($line.StartsWith("#")) {
            return
        }
        $parts = $line -split "=", 2
        if ($parts.Count -lt 2) {
            return
        }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Trim('"')
        }
        if ($value.StartsWith("'") -and $value.EndsWith("'")) {
            $value = $value.Trim("'")
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
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

$venvPath = Join-Path $InstallDir ".venv"
$venvActivate = Join-Path $venvPath "Scripts\\Activate.ps1"
$venvPython = Join-Path $venvPath "Scripts\\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Brak .venv - tworzenie srodowiska w $venvPath"
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Nie znaleziono python.exe w PATH."
    }
    python -m venv $venvPath
}
if (-not (Test-Path $venvActivate)) {
    throw "Nie znaleziono aktywatora .venv ($venvActivate)."
}

$envPath = Join-Path $InstallDir ".env"
Import-DotEnv -Path $envPath

if ($ServiceNames.Count -eq 0) {
    $ServiceNames = @($ServiceName)
}

$servicesToRestart = @()
foreach ($name in $ServiceNames) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Host "Usluga $name nie istnieje - pomijam."
        continue
    }
    if ($service.Status -eq "Running") {
        Write-Host "Zatrzymywanie uslugi $name"
        Stop-Service -Name $name -Force -ErrorAction Stop
        $servicesToRestart += $name
        Start-Sleep -Seconds 2
    }
}

$updateSucceeded = $false

try {
    Write-Host "git fetch $GitRemote --tags"
    git fetch $GitRemote --tags
    Write-Host "git pull $GitRemote $GitBranch --ff-only"
    git pull $GitRemote $GitBranch --ff-only

    Write-Host "Aktywacja .venv"
    . $venvActivate

    if (-not $SkipPip) {
        Write-Host "Aktualizacja zaleznosci w .venv"
        python -m pip install -r requirements.txt
    }

    if (-not $SkipPreCommit) {
        Write-Host "Uruchamianie pre-commit"
        pre-commit run --all-files
    }

    if (-not $SkipTests) {
        Write-Host "Uruchamianie testow jednostkowych"
        python -m unittest discover -s tests
    }

    $updateSucceeded = $true
}
finally {
    if ($servicesToRestart.Count -gt 0) {
        if ($updateSucceeded -or $ForceStartOnFailure) {
            foreach ($name in $servicesToRestart) {
                try {
                    Write-Host "Ponowne uruchamianie uslugi $name"
                    Start-Service -Name $name -ErrorAction Stop
                } catch {
                    Write-Warning "Nie udalo sie uruchomic uslugi $name. Sprawdz dzienniki."
                }
            }
        } else {
            Write-Warning "Aktualizacja nie powiodla sie - uslugi pozostaja zatrzymane."
        }
    }
}
