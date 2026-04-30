param(
    [string]$InstallDir = "D:\CTIP",
    [string]$GitRemote = "origin",
    [string]$GitBranch = "codex/fix-public-form-checkbox-422",
    [string]$TargetCommit = "24d3943",
    [string]$ExpectedAlembicHead = "8d7a3b9e4c11",
    [string[]]$ServiceNames = @("CollectorService", "CTIP-Web", "CTIP-SMS", "CTIP-FormsPublic"),
    [string]$HealthUrl = "http://127.0.0.1:8000/health",
    [switch]$Apply,
    [switch]$SkipPip,
    [switch]$SkipPreCommit,
    [switch]$SkipTests,
    [switch]$AllowDirtyRepo,
    [switch]$AllowNewerCommit
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Host "[$stamp] $Message"
}

function Fail {
    param([string]$Message)
    throw "BLAD: $Message"
}

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Fail "Uruchom PowerShell w trybie Administratora."
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

function Invoke-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        Fail "Polecenie git nie powiodlo sie: git $($GitArgs -join ' ')"
    }
}

Assert-Admin

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "Brak git w PATH."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "Brak python w PATH."
}

if (-not (Test-Path $InstallDir)) {
    Fail "Katalog nie istnieje: $InstallDir"
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

$updateScript = Join-Path $InstallDir "scripts\windows\update_ctip.ps1"
$envPath = Join-Path $InstallDir ".env"
$venvActivate = Join-Path $InstallDir ".venv\Scripts\Activate.ps1"
$knowledgeIndex = Join-Path $InstallDir "docs\firebird\knowledge\firebird_ms_knowledge.json"
$knowledgeBuildScript = Join-Path $InstallDir "scripts\build_firebird_knowledge_index.py"

if (-not (Test-Path $updateScript)) {
    Fail "Brak skryptu: $updateScript"
}
if (-not (Test-Path $envPath)) {
    Fail "Brak pliku .env w: $InstallDir"
}
if (-not (Test-Path $knowledgeIndex)) {
    Write-Log "UWAGA: Brak indeksu wiedzy Firebird przed aktualizacja. Sprawdze ponownie po git pull."
}

Import-DotEnv -Path $envPath

if ($env:PGDATABASE -eq "ctip_test") {
    Fail "PGDATABASE=ctip_test. To nie jest konfiguracja produkcyjna."
}
if ($env:SMS_TEST_MODE -eq "true") {
    Fail "SMS_TEST_MODE=true. Zatrzymano wdrozenie produkcyjne."
}
if (-not $env:OPENAI_API_CHAT_KP -and -not $env:OPENAI_API_KEY) {
    Write-Log "UWAGA: Brak OPENAI_API_CHAT_KP i OPENAI_API_KEY w .env."
}

if (-not $AllowDirtyRepo) {
    $dirtyTracked = git status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0) {
        Fail "Nie udalo sie odczytac statusu git."
    }
    if ($dirtyTracked) {
        Fail "Repozytorium ma lokalne zmiany tracked. Commit/stash przed wdrozeniem albo uruchom z -AllowDirtyRepo."
    }
}

Write-Log "Pobieram metadane git"
Invoke-Git -GitArgs @("fetch", $GitRemote, "--tags")

$remoteBranchRef = "$GitRemote/$GitBranch"
& git rev-parse --verify $remoteBranchRef *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "Brak zdalnej galezi: $remoteBranchRef"
}

& git merge-base --is-ancestor $TargetCommit $remoteBranchRef
if ($LASTEXITCODE -ne 0) {
    Fail "Commit $TargetCommit nie nalezy do $remoteBranchRef"
}

if (-not $Apply) {
    Write-Log "Tryb podgladu. Wykonaj realne wdrozenie poleceniem:"
    Write-Host ".\inbox\deploy_prod_ctip_assistant_2026-04-30.ps1 -Apply"
    exit 0
}

Write-Log "Start wdrozenia produkcyjnego na branchu $GitBranch"

$updateParams = @{
    InstallDir = $InstallDir
    GitRemote = $GitRemote
    GitBranch = $GitBranch
    ServiceNames = $ServiceNames
}
if ($SkipPip) { $updateParams.SkipPip = $true }
if ($SkipPreCommit) { $updateParams.SkipPreCommit = $true }
if ($SkipTests) { $updateParams.SkipTests = $true }

& $updateScript @updateParams
if ($LASTEXITCODE -ne 0) {
    Fail "Skrypt update_ctip.ps1 zakonczyl sie bledem."
}

$currentHead = (git rev-parse --short HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    Fail "Nie udalo sie odczytac HEAD po aktualizacji."
}

if ($AllowNewerCommit) {
    & git merge-base --is-ancestor $TargetCommit HEAD
    if ($LASTEXITCODE -ne 0) {
        Fail "HEAD nie zawiera docelowego commitu $TargetCommit (HEAD=$currentHead)."
    }
} else {
    if ($currentHead -ne $TargetCommit) {
        Fail "Po aktualizacji HEAD=$currentHead, oczekiwano dokladnie $TargetCommit."
    }
}

if (-not (Test-Path $venvActivate)) {
    Fail "Brak aktywatora .venv: $venvActivate"
}

if (-not (Test-Path $knowledgeIndex)) {
    if (Test-Path $knowledgeBuildScript) {
        Write-Log "Brak indeksu wiedzy po aktualizacji. Uruchamiam generator."
        . $venvActivate
        python $knowledgeBuildScript
        if ($LASTEXITCODE -ne 0) {
            Fail "Generator indeksu wiedzy Firebird zakonczyl sie bledem."
        }
    }
}
if (-not (Test-Path $knowledgeIndex)) {
    Fail "Brak indeksu wiedzy Firebird po aktualizacji: $knowledgeIndex"
}

Write-Log "Migracje Alembic"
. $venvActivate
Import-DotEnv -Path $envPath
python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Fail "alembic upgrade head zakonczyl sie bledem."
}

$alembicCurrentRaw = python -m alembic current
if ($LASTEXITCODE -ne 0) {
    Fail "Nie udalo sie odczytac alembic current."
}
$alembicCurrent = ""
if ($alembicCurrentRaw -match "^([0-9a-f]+)\s") {
    $alembicCurrent = $matches[1]
}
if (-not $alembicCurrent) {
    Fail "Nie udalo sie sparsowac rewizji Alembic."
}
if ($ExpectedAlembicHead -and $alembicCurrent -ne $ExpectedAlembicHead) {
    Fail "Rewizja Alembic=$alembicCurrent, oczekiwano $ExpectedAlembicHead."
}

Write-Log "Smoke test: $HealthUrl"
try {
    $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 30
} catch {
    Fail "Healthcheck nie osiagnal aplikacji: $HealthUrl"
}
if ($response.StatusCode -ne 200) {
    Fail "Healthcheck zwrocil HTTP $($response.StatusCode), oczekiwano 200."
}

Write-Log "Wdrozenie zakonczone poprawnie."
Write-Host "HEAD: $currentHead"
Write-Host "Alembic: $alembicCurrent"
Write-Host "Health: HTTP $($response.StatusCode)"
