param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{40}$")]
    [string]$ReleaseCommit,
    [string]$InstallDir = "D:\CTIP",
    [string]$GitRemote = "origin",
    [string]$ReleaseBranch = "release/shipping-prod-current-2026-08-27",
    [string]$ExpectedCurrentCommit = "e17ad41c2651039d8b00464ddc6dc86a5549b240",
    [string]$ExpectedAlembicCurrent = "d8f1a2b3c4e5",
    [string]$ExpectedAlembicHead = "f9a0b1c2d3e4",
    [string]$ExpectedFirebirdHost = "192.168.0.8",
    [string]$CandidateDir = "D:\CTIP_shipping_candidate",
    [string]$HealthUrl = "http://127.0.0.1:8000/health",
    [string]$CandidateHealthUrl = "http://127.0.0.1:8002/health",
    [string]$FormsHealthUrl = "http://127.0.0.1:8100/health",
    [string]$GbakPath = "",
    [switch]$Apply,
    [switch]$KeepCandidate
)

$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

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
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Fail "Uruchom PowerShell w trybie Administratora."
    }
}

function Invoke-Git {
    param([string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "Polecenie git nie powiodlo sie: git $($Arguments -join ' ')"
    }
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Fail "Brak pliku srodowiskowego: $Path"
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) { return }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) { $value = $value.Trim('"') }
        if ($value.StartsWith("'") -and $value.EndsWith("'")) { $value = $value.Trim("'") }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Require-Env {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        Fail "Brak wymaganej zmiennej $Name w .env."
    }
}

function Assert-EnvEquals {
    param([string]$Name, [string]$Expected)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($value -ne $Expected) {
        Fail "Zmienna $Name ma wartosc '$value', oczekiwano '$Expected'."
    }
}

function Invoke-Python {
    param(
        [string]$PythonExe,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    Push-Location $WorkingDirectory
    try {
        $output = & $PythonExe @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        if ($output) { Write-Host ($output -join [Environment]::NewLine) }
        Fail "Polecenie Python zakonczylo sie bledem: python $($Arguments -join ' ')"
    }
    return @($output)
}

function Assert-Http200 {
    param([string]$Url, [string]$Label)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30
    } catch {
        Fail "$Label nie odpowiada: $Url"
    }
    if ($response.StatusCode -ne 200) {
        Fail "$Label zwrocil HTTP $($response.StatusCode), oczekiwano 200."
    }
}

function Assert-ServiceRunning {
    param([string]$Name)
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        Fail "Brak wymaganej uslugi Windows: $Name"
    }
    if ($service.Status -ne "Running") {
        Fail "Usluga $Name nie dziala przed wdrozeniem (stan: $($service.Status))."
    }
}

function Test-AllowedReleasePath {
    param([string]$Path)
    $exact = @(
        ".env.example",
        ".env.test.example",
        "README.md",
        "app/api/router.py",
        "app/core/config.py",
        "app/main.py",
        "app/models/__init__.py",
        "app/schemas/admin.py",
        "app/services/section_permissions.py",
        "app/static/admin/admin.js",
        "app/static/root/section_switcher.js",
        "app/templates/admin/partials/users.html",
        "app/templates/root/choice.html",
        "docs/baza/changes.md",
        "docs/baza/schema_ctip.sql",
        "docs/instal/wdrozenie_shipping_prod_2026-08-27.md",
        "docs/projekt/wysylki_dpd.md",
        "pyproject.toml",
        "tests/test_db_schema.py",
        "tests/test_admin_backend.py",
        "tests/test_api_auth.py",
        "tests/test_settings.py",
        "tests/test_shipping.py",
        "tests/test_shipping_release.py"
    )
    if ($exact -contains $Path) { return $true }
    $prefixes = @(
        "alembic/versions/f9a0b1c2d3e4_",
        "app/api/routes/admin_shipping.py",
        "app/models/shipping.py",
        "app/schemas/shipping.py",
        "app/services/dpd_shipping.py",
        "app/services/firebird_runtime.py",
        "app/services/shipping_",
        "app/static/shipping/",
        "app/templates/shipping/",
        "app/web/shipping_ui.py",
        "scripts/windows/deploy_shipping_prod_2026-08-27.ps1"
    )
    foreach ($prefix in $prefixes) {
        if ($Path.StartsWith($prefix, [StringComparison]::Ordinal)) { return $true }
    }
    return $false
}

function Stop-Candidate {
    param([System.Diagnostics.Process]$Process)
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit(10000) | Out-Null
    }
}

Assert-Admin

foreach ($command in @("git", "powershell.exe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        Fail "Brak polecenia w PATH: $command"
    }
}
if (-not (Test-Path $InstallDir)) { Fail "Katalog nie istnieje: $InstallDir" }

$InstallDir = (Resolve-Path $InstallDir).Path
$envPath = Join-Path $InstallDir ".env"
$pythonExe = Join-Path $InstallDir ".venv\Scripts\python.exe"
$backupScript = Join-Path $InstallDir "scripts\windows\backup_prod_databases.ps1"
$reportDirectory = Join-Path $InstallDir "docs\raport"
$serviceNames = @("CollectorService", "CTIP-Web", "CTIP-SMS", "CTIP-FormsPublic")

if (-not (Test-Path $pythonExe)) { Fail "Brak produkcyjnego .venv: $pythonExe" }
if (-not (Test-Path $backupScript)) { Fail "Brak skryptu backupu: $backupScript" }
if (-not (Test-Path $reportDirectory)) { Fail "Brak lokalnego katalogu statycznego: $reportDirectory" }

Set-Location $InstallDir
Import-DotEnv -Path $envPath

foreach ($name in @(
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "FB_HOST", "FB_DATABASE", "FB_USER", "FB_PASSWORD",
    "DPD_LOGIN", "DPD_PASSWORD", "DPD_MASTER_FID", "DPD_PAYER_FID",
    "DPD_SENDER_COMPANY", "DPD_SENDER_CONTACT", "DPD_SENDER_STREET",
    "DPD_SENDER_POSTAL_CODE", "DPD_SENDER_CITY", "DPD_SENDER_PHONE", "DPD_SENDER_EMAIL"
)) { Require-Env -Name $name }

if ($env:PGDATABASE -eq "ctip_test") { Fail "PGDATABASE=ctip_test; oczekiwano produkcji." }
Assert-EnvEquals -Name "FB_HOST" -Expected $ExpectedFirebirdHost
Assert-EnvEquals -Name "SMS_TEST_MODE" -Expected "false"
Assert-EnvEquals -Name "FB_ALLOW_WRITES" -Expected "true"
Assert-EnvEquals -Name "SHIPPING_ENABLED" -Expected "true"
Assert-EnvEquals -Name "SHIPPING_CATALOG_MUTATIONS_ENABLED" -Expected "true"
Assert-EnvEquals -Name "SHIPPING_FULFILLMENT_ENABLED" -Expected "false"
Assert-EnvEquals -Name "SHIPPING_TEST_FIREBIRD_WRITES" -Expected "false"
Assert-EnvEquals -Name "SHIPPING_COMPATIBILITY_WEB_ENABLED" -Expected "false"
Assert-EnvEquals -Name "DPD_ENABLED" -Expected "false"
Assert-EnvEquals -Name "DPD_MODE" -Expected "production"
if (-not [string]::IsNullOrWhiteSpace($env:DPD_API_URL)) {
    Fail "DPD_API_URL musi pozostac puste, aby adapter wybral oficjalny adres produkcyjny."
}

foreach ($name in $serviceNames) { Assert-ServiceRunning -Name $name }
Assert-Http200 -Url $HealthUrl -Label "CTIP-Web"
Assert-Http200 -Url $FormsHealthUrl -Label "CTIP-FormsPublic"

$dirtyTracked = git status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) { Fail "Nie udalo sie sprawdzic statusu repozytorium." }
if ($dirtyTracked) { Fail "Repozytorium produkcyjne ma lokalne zmiany tracked." }

$currentCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { Fail "Nie udalo sie odczytac HEAD." }
if ($currentCommit -ne $ExpectedCurrentCommit) {
    Fail "HEAD=$currentCommit, oczekiwano bezpiecznej bazy $ExpectedCurrentCommit."
}

Write-Log "Pobieram release z GitHub"
Invoke-Git -Arguments @("fetch", $GitRemote, $ReleaseBranch)
& git cat-file -e "$ReleaseCommit^{commit}"
if ($LASTEXITCODE -ne 0) { Fail "Nie znaleziono commitu release: $ReleaseCommit" }
& git merge-base --is-ancestor $ExpectedCurrentCommit $ReleaseCommit
if ($LASTEXITCODE -ne 0) { Fail "Release nie bazuje na zatwierdzonym commicie produkcyjnym." }

$fetchedCommit = (git rev-parse "FETCH_HEAD^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or $fetchedCommit -ne $ReleaseCommit) {
    Fail "FETCH_HEAD=$fetchedCommit, oczekiwano dokladnie $ReleaseCommit."
}

$changedPaths = @(git diff --name-only $ExpectedCurrentCommit $ReleaseCommit)
if ($LASTEXITCODE -ne 0) { Fail "Nie udalo sie odczytac zakresu zmian release." }
$unexpectedPaths = @($changedPaths | Where-Object { -not (Test-AllowedReleasePath -Path $_) })
if ($unexpectedPaths.Count -gt 0) {
    Fail "Release zmienia niedozwolone pliki: $($unexpectedPaths -join ', ')"
}

if (Test-Path $CandidateDir) {
    Fail "Katalog kandydata juz istnieje: $CandidateDir. Usun go recznie po weryfikacji poprzedniego wdrozenia."
}

if (-not $Apply) {
    Write-Log "Dry-run zakonczony. Kod, bazy i uslugi nie zostaly zmienione."
    Write-Host "Zakres release: $($changedPaths.Count) plikow"
    Write-Host "Uruchomienie: & '$($MyInvocation.MyCommand.Path)' -ReleaseCommit $ReleaseCommit -Apply"
    exit 0
}

$candidateProcess = $null
$cutoverStarted = $false
try {
    Write-Log "Wykonuje zweryfikowany backup PostgreSQL i Firebird"
    $backupParams = @{ InstallDir = $InstallDir }
    if ($GbakPath) { $backupParams.GbakPath = $GbakPath }
    & $backupScript @backupParams
    if (-not $?) { Fail "Backup produkcyjny zakonczyl sie bledem." }

    Write-Log "Tworze odseparowany worktree kandydata"
    Invoke-Git -Arguments @("worktree", "add", "--detach", $CandidateDir, $ReleaseCommit)
    $candidateReportDirectory = Join-Path $CandidateDir "docs\raport"
    if (Test-Path $candidateReportDirectory) {
        $unexpectedReportFiles = @(
            Get-ChildItem -Path $candidateReportDirectory -Force |
                Where-Object { $_.Name -ne ".gitkeep" }
        )
        if ($unexpectedReportFiles.Count -gt 0) {
            Fail "Katalog raportu kandydata zawiera nieoczekiwane pliki."
        }
        Remove-Item -Path $candidateReportDirectory -Recurse -Force
    }
    New-Item -ItemType Junction -Path $candidateReportDirectory -Target $reportDirectory | Out-Null

    Write-Log "Synchronizuje zaleznosci wspolnego .venv"
    Invoke-Python -PythonExe $pythonExe -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $CandidateDir "requirements.txt")
    ) -WorkingDirectory $CandidateDir | Out-Null

    Write-Log "Waliduje kod kandydata"
    Invoke-Python -PythonExe $pythonExe -Arguments @(
        "-m", "compileall", "-q", "app"
    ) -WorkingDirectory $CandidateDir | Out-Null
    $validationFlags = @{
        SHIPPING_ENABLED = $env:SHIPPING_ENABLED
        SHIPPING_CATALOG_MUTATIONS_ENABLED = $env:SHIPPING_CATALOG_MUTATIONS_ENABLED
        SHIPPING_FULFILLMENT_ENABLED = $env:SHIPPING_FULFILLMENT_ENABLED
        DPD_ENABLED = $env:DPD_ENABLED
    }
    try {
        $env:SHIPPING_ENABLED = "false"
        $env:SHIPPING_CATALOG_MUTATIONS_ENABLED = "false"
        $env:SHIPPING_FULFILLMENT_ENABLED = "false"
        $env:DPD_ENABLED = "false"
        Invoke-Python -PythonExe $pythonExe -Arguments @(
            "-m", "unittest", "tests.test_settings", "tests.test_shipping_release"
        ) -WorkingDirectory $CandidateDir | Out-Null
    } finally {
        foreach ($name in $validationFlags.Keys) {
            [Environment]::SetEnvironmentVariable($name, $validationFlags[$name], "Process")
        }
    }

    $alembicHeads = Invoke-Python -PythonExe $pythonExe -Arguments @(
        "-m", "alembic", "heads"
    ) -WorkingDirectory $CandidateDir
    if (($alembicHeads -join "`n") -notmatch "\b$ExpectedAlembicHead\b") {
        Fail "Kandydat nie wskazuje oczekiwanego heada Alembic $ExpectedAlembicHead."
    }

    $alembicBefore = Invoke-Python -PythonExe $pythonExe -Arguments @(
        "-m", "alembic", "current"
    ) -WorkingDirectory $CandidateDir
    if (($alembicBefore -join "`n") -notmatch "\b$ExpectedAlembicCurrent\b") {
        Fail "Produkcja nie jest na oczekiwanej rewizji Alembic $ExpectedAlembicCurrent."
    }

    Write-Log "Uruchamiam addytywna migracje Shipping"
    Invoke-Python -PythonExe $pythonExe -Arguments @(
        "-m", "alembic", "upgrade", $ExpectedAlembicHead
    ) -WorkingDirectory $CandidateDir | Out-Null
    $alembicAfter = Invoke-Python -PythonExe $pythonExe -Arguments @(
        "-m", "alembic", "current"
    ) -WorkingDirectory $CandidateDir
    if (($alembicAfter -join "`n") -notmatch "\b$ExpectedAlembicHead\b") {
        Fail "Migracja nie osiagnela rewizji $ExpectedAlembicHead."
    }

    Assert-Http200 -Url $HealthUrl -Label "CTIP-Web przed testem kandydata"
    foreach ($name in @("CollectorService", "CTIP-SMS", "CTIP-FormsPublic")) {
        Assert-ServiceRunning -Name $name
    }

    Write-Log "Uruchamiam kandydata na 127.0.0.1:8002 bez schedulerow"
    $candidateLogDir = Join-Path $InstallDir "logs\shipping_deploy_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    New-Item -ItemType Directory -Path $candidateLogDir -Force | Out-Null
    $env:SHIPPING_ENABLED = "true"
    $env:SHIPPING_CATALOG_MUTATIONS_ENABLED = "false"
    $env:SHIPPING_FULFILLMENT_ENABLED = "false"
    $env:DPD_ENABLED = "false"
    $candidateProcess = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8002", "--lifespan", "off") `
        -WorkingDirectory $CandidateDir `
        -RedirectStandardOutput (Join-Path $candidateLogDir "candidate_stdout.log") `
        -RedirectStandardError (Join-Path $candidateLogDir "candidate_stderr.log") `
        -PassThru
    Start-Sleep -Seconds 5
    if ($candidateProcess.HasExited) { Fail "Proces kandydata zakonczyl sie przed smoke-testem." }
    Assert-Http200 -Url $CandidateHealthUrl -Label "Kandydat Shipping"
    Assert-Http200 -Url "http://127.0.0.1:8002/shipping/v2" -Label "Widok Shipping kandydata"
    Assert-Http200 -Url $HealthUrl -Label "CTIP-Web podczas testu kandydata"
    Assert-Http200 -Url $FormsHealthUrl -Label "CTIP-FormsPublic podczas testu kandydata"

    Write-Log "Rozpoczynam cutover; zatrzymuje wylacznie CTIP-Web"
    $cutoverStarted = $true
    Stop-Service -Name "CTIP-Web" -Force
    $webService = Get-Service -Name "CTIP-Web"
    $webService.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))

    Set-Location $InstallDir
    Invoke-Git -Arguments @("checkout", "--detach", $ReleaseCommit)
    Start-Service -Name "CTIP-Web"
    $webService = Get-Service -Name "CTIP-Web"
    $webService.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
    Start-Sleep -Seconds 5

    Assert-Http200 -Url $HealthUrl -Label "CTIP-Web po cutover"
    Assert-Http200 -Url "http://127.0.0.1:8000/shipping/v2" -Label "Shipping po cutover"
    Assert-Http200 -Url $FormsHealthUrl -Label "CTIP-FormsPublic po cutover"
    foreach ($name in @("CollectorService", "CTIP-SMS", "CTIP-FormsPublic")) {
        Assert-ServiceRunning -Name $name
    }

    $deployedCommit = (git rev-parse HEAD).Trim()
    if ($deployedCommit -ne $ReleaseCommit) { Fail "Po cutover wdrozono inny commit: $deployedCommit" }

    Write-Log "Wdrozenie fazy odczytowej zakonczone poprawnie"
    Write-Host "HEAD: $deployedCommit"
    Write-Host "Alembic: $ExpectedAlembicHead"
    Write-Host "Shipping: wlaczony; katalog: wlaczony; realizacja i DPD: zablokowane"
} catch {
    $failure = $_
    Write-Warning "Wdrozenie przerwane: $($failure.Exception.Message)"
    if ($cutoverStarted) {
        Write-Warning "Cofam kod do $ExpectedCurrentCommit; addytywny schemat Shipping pozostaje."
        Stop-Service -Name "CTIP-Web" -Force -ErrorAction SilentlyContinue
        Set-Location $InstallDir
        & git checkout --detach $ExpectedCurrentCommit
        if ($LASTEXITCODE -ne 0) { Write-Warning "Automatyczny checkout rollbacku nie powiodl sie." }
        Start-Service -Name "CTIP-Web" -ErrorAction SilentlyContinue
    }
    throw $failure
} finally {
    Stop-Candidate -Process $candidateProcess
    if ((Test-Path $CandidateDir) -and -not $KeepCandidate) {
        Set-Location $InstallDir
        & git worktree remove --force $CandidateDir
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Nie udalo sie usunac worktree kandydata: $CandidateDir"
        }
    }
}
