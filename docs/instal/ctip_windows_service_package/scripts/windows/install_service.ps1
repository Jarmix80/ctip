param(
    [string]$InstallDir = "D:\\CTIP",
    [string]$ServiceName = "CollectorService",
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"

function Get-PythonVersions {
    param(
        [string]$Launcher
    )

    if (-not (Get-Command $Launcher -ErrorAction SilentlyContinue)) {
        return "brak $Launcher"
    }

    try {
        $output = & $Launcher "-0p" 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            return ($output -join "; ")
        }
    } catch {
    }

    return "nieznane (uzyj py --list)"
}

function Assert-PythonVersionAvailable {
    param(
        [string]$Launcher,
        [string]$Version
    )

    if ($Launcher -ne "py" -or [string]::IsNullOrWhiteSpace($Version)) {
        return
    }

    try {
        & $Launcher "-$Version" "-c" "import sys; print(sys.executable)" | Out-Null
    } catch {
        $available = Get-PythonVersions -Launcher $Launcher
        throw "Interpreter $Launcher -$Version nie jest dostepny. Sprawdz liste (py --list / py -0p) i zainstaluj Python $Version x64. Dostepne: $available"
    }
}

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

    Assert-PythonVersionAvailable -Launcher $Launcher -Version $Version

    if (Test-Path $TargetDir) {
        $pyvenvCfg = Join-Path $TargetDir "pyvenv.cfg"
        if ((Test-Path $pyvenvCfg) -and (-not [string]::IsNullOrWhiteSpace($Version))) {
            $versionLine = Get-Content -Path $pyvenvCfg | Where-Object { $_ -match '^version\\s*=\\s*(.+)$' } | Select-Object -First 1
            if ($versionLine) {
                $currentVersion = ($versionLine -replace '^version\\s*=\\s*', '').Trim()
                if (-not $currentVersion.StartsWith($Version)) {
                    throw ".venv juz istnieje z Python $currentVersion. Usun katalog $TargetDir i uruchom skrypt ponownie, aby zbudowac srodowisko na Python $Version."
                }
            }
        }
        return
    }

    if (-not (Get-Command $Launcher -ErrorAction SilentlyContinue)) {
        throw "Nie znaleziono polecenia $Launcher. Zainstaluj Python Launcher (py.exe) lub ustaw parametr -PythonLauncher."
    }

    Write-Host "Tworzenie srodowiska .venv w $TargetDir"
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
    throw "Nie udalo sie odnalezc $pythonExe. Sprawdz poprawnosc instalacji srodowiska wirtualnego."
}

Write-Host "Aktualizacja pip i instalacja zaleznosci"
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r requirements.txt

Write-Host "Rejestracja komponentow pywin32 (servicemanager)"
$postinstallRan = $false
try {
    & $pythonExe -m pywin32_postinstall -install
    $postinstallRan = $true
} catch {
    $postScript = Join-Path $venvPath "Scripts\\pywin32_postinstall.py"
    if (Test-Path $postScript) {
        try {
            & $pythonExe $postScript -install
            $postinstallRan = $true
        } catch {
            Write-Warning "pywin32_postinstall.py wywolane jako skrypt zwrocilo blad: $_"
        }
    } else {
        Write-Warning "Nie znaleziono pywin32_postinstall (brak modulu i skryptu w Scripts)."
    }
}

if (-not $postinstallRan) {
    Write-Warning "pywin32_postinstall nie zostal wykonany. Jesli usluga nie startuje, odpal recznie: $pythonExe -m pip install --force-reinstall pywin32 && $pythonExe -m pywin32_postinstall -install"
}

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
