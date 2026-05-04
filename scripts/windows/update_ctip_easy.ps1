param(
    [string]$InstallDir = "D:\\CTIP",
    [string[]]$ServiceNames = @("CollectorService", "CTIP-Web", "CTIP-SMS"),
    [string]$GitRemote = "origin",
    [string]$GitBranch = "main",
    [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"

# PowerShell 7+ potrafi traktować stderr z narzędzi natywnych (git/python)
# jako ErrorRecord mimo poprawnego kodu wyjścia. Wyłączamy to i opieramy
# walidację na kodach wyjścia.
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Uruchom PowerShell w trybie Administratora."
    }
}

function New-LogFile {
    param([string]$BaseDir, [string]$Prefix)
    if (-not (Test-Path $BaseDir)) {
        New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null
    }
    $stamp = (Get-Date).ToString("yyyy-MM-dd")
    return Join-Path $BaseDir ("{0}_{1}.log" -f $Prefix, $stamp)
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[{0}] [{1}] {2}" -f $ts, $Level, $Message
    Write-Host $line
    Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
}

function Get-GitHead {
    param([string]$Ref = "HEAD")
    $head = git rev-parse $Ref 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return $head.Trim()
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

$logDirPath = Join-Path $InstallDir "logs\\maintenance"
$script:LogPath = New-LogFile -BaseDir $logDirPath -Prefix "update_easy"

Write-Log "Start aktualizacji easy."

$headBefore = Get-GitHead "HEAD"
if (-not $headBefore) {
    throw "Nie mozna odczytac HEAD."
}

$dirty = git status --porcelain
if ($dirty) {
    Write-Log "Repo zawiera lokalne zmiany - pull moze sie nie udac." "WARN"
}

Write-Log ("git fetch {0} --tags" -f $GitRemote)
git fetch $GitRemote --tags

$remoteRef = "$GitRemote/$GitBranch"
$headRemote = Get-GitHead $remoteRef
if (-not $headRemote) {
    throw "Nie znaleziono ref $remoteRef."
}

$needsRestart = $ForceRestart -or ($headRemote -ne $headBefore)
if (-not $needsRestart) {
    Write-Log "Brak nowych commitow - pomijam restart uslug."
    exit 0
}

$servicesToRestart = @()
foreach ($name in $ServiceNames) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Log "Usluga $name nie istnieje - pomijam." "WARN"
        continue
    }
    if ($service.Status -eq "Running") {
        Write-Log "Zatrzymywanie uslugi $name."
        Stop-Service -Name $name -Force -ErrorAction Stop
        $servicesToRestart += $name
        Start-Sleep -Seconds 2
    } else {
        Write-Log "Usluga $name nie jest uruchomiona (status: $($service.Status))." "WARN"
    }
}

$updateSucceeded = $false
try {
    Write-Log ("git pull {0} {1} --ff-only" -f $GitRemote, $GitBranch)
    git pull $GitRemote $GitBranch --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull nie powiodl sie."
    }
    $updateSucceeded = $true
}
finally {
    if ($servicesToRestart.Count -gt 0) {
        foreach ($name in $servicesToRestart) {
            try {
                Write-Log "Ponowne uruchamianie uslugi $name."
                Start-Service -Name $name -ErrorAction Stop
            } catch {
                Write-Log ("Nie udalo sie uruchomic uslugi {0}: {1}" -f $name, $_.Exception.Message) "WARN"
            }
        }
    }
}

if (-not $updateSucceeded) {
    Write-Log "Aktualizacja nieudana." "ERROR"
    exit 1
}

$headAfter = Get-GitHead "HEAD"
Write-Log ("Zaktualizowano {0} -> {1}." -f $headBefore, $headAfter)
Write-Log "Zakonczenie."
exit 0
