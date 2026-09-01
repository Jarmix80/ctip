param(
    [string]$InstallDir = "D:\CTIP",
    [Parameter(Mandatory = $true)][string]$PlanJsonBase64,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

Import-Module (Join-Path $PSScriptRoot "CtipDeployment.Common.psm1") -Force

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Wdrożenie wymaga sesji administratora."
    }
}

function Get-Plan {
    try {
        $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PlanJsonBase64))
        return $json | ConvertFrom-Json
    }
    catch {
        throw "Plan wdrożenia nie jest poprawnym JSON zakodowanym Base64."
    }
}

function Assert-FullCommit {
    param([string]$Label, [string]$Value)
    if ($Value -notmatch '^[0-9a-fA-F]{40}$') {
        throw "$Label musi być pełnym SHA-1 commita."
    }
}

function Get-GitText {
    param([string[]]$Arguments, [string]$Label)
    $result = Invoke-CtipNative -FilePath "git.exe" -ArgumentList $Arguments -Label $Label
    return (@($result.Output) -join "`n").Trim()
}

function Get-AlembicRevisions {
    param([string[]]$Arguments, [string]$WorkingDirectory, [string]$Label)
    Push-Location $WorkingDirectory
    try {
        $pythonPath = Join-Path $InstallDir ".venv\Scripts\python.exe"
        $result = Invoke-CtipNative -FilePath $pythonPath -ArgumentList (@("-m", "alembic") + $Arguments) -Label $Label
    }
    finally {
        Pop-Location
    }
    $revisions = @()
    foreach ($line in $result.Output) {
        if ([string]$line -match '^\s*([0-9a-fA-F]{12,40})\b') {
            $revisions += $Matches[1].ToLowerInvariant()
        }
    }
    return @($revisions | Sort-Object -Unique)
}

function Assert-AlembicRevision {
    param([string]$Expected, [string]$WorkingDirectory, [string]$Label)
    $current = @(Get-AlembicRevisions -Arguments @("current") -WorkingDirectory $WorkingDirectory -Label $Label)
    if ($current.Count -ne 1 -or $current[0] -ne $Expected.ToLowerInvariant()) {
        throw "Nieoczekiwana rewizja Alembic: $($current -join ', '); oczekiwano $Expected."
    }
}

function Assert-ServiceRunning {
    param([string]$Name)
    $service = Get-Service -Name $Name -ErrorAction Stop
    if ($service.Status -ne "Running") {
        throw "Usługa $Name nie działa."
    }
}

function Assert-Endpoints {
    param([object[]]$Endpoints)
    foreach ($endpoint in $Endpoints) {
        Test-CtipHttpEndpoint `
            -Label ([string]$endpoint.label) `
            -Url ([string]$endpoint.url) `
            -ExpectedStatus ([int]$endpoint.status)
    }
}

function Assert-AllowedChanges {
    param([string]$CurrentCommit, [string]$ReleaseCommit, [string[]]$AllowedPaths)
    $changedText = Get-GitText `
        -Arguments @("-C", $InstallDir, "diff", "--name-only", "$CurrentCommit..$ReleaseCommit") `
        -Label "Kontrola zakresu zmian release"
    $rejected = @()
    foreach ($changedPath in @($changedText -split "`n")) {
        if (-not $changedPath) {
            continue
        }
        $allowed = $false
        foreach ($prefixValue in $AllowedPaths) {
            $prefix = ([string]$prefixValue).Trim("/")
            if ($changedPath -eq $prefix -or $changedPath.StartsWith("$prefix/")) {
                $allowed = $true
                break
            }
        }
        if (-not $allowed) {
            $rejected += $changedPath
        }
    }
    if ($rejected.Count -gt 0) {
        throw "Release zmienia niedozwolone ścieżki: $($rejected -join ', ')."
    }
}

function Assert-CurrentWebLog {
    $registryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\CTIP-Web\Parameters"
    $stderrPath = (Get-ItemProperty -LiteralPath $registryPath -Name AppStderr -ErrorAction SilentlyContinue).AppStderr
    if (-not $stderrPath) {
        $stderrPath = Join-Path $InstallDir "logs\web\web_stderr.log"
    }
    $currentLines = @(Get-CtipCurrentStartLog -Path $stderrPath -ServiceName "CTIP-Web")
    $criticalPatterns = @("Traceback (most recent call last)", "Application startup failed", "Could not import module")
    foreach ($pattern in $criticalPatterns) {
        if ($currentLines | Select-String -Pattern $pattern -SimpleMatch) {
            throw "Bieżący start CTIP-Web zawiera błąd: $pattern."
        }
    }
    Write-CtipStatus -Level "OK" -Message "Log CTIP-Web od bieżącego startu nie zawiera błędów krytycznych."
}

Assert-Administrator
if (-not (Test-Path -LiteralPath $InstallDir)) {
    throw "Katalog instalacyjny nie istnieje: $InstallDir"
}
$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$plan = Get-Plan
Assert-FullCommit -Label "expected_current" -Value ([string]$plan.expected_current)
Assert-FullCommit -Label "release" -Value ([string]$plan.release)

$expectedCurrent = ([string]$plan.expected_current).ToLowerInvariant()
$release = ([string]$plan.release).ToLowerInvariant()
$alembicBefore = ([string]$plan.alembic_before).ToLowerInvariant()
$alembicAfter = ([string]$plan.alembic_after).ToLowerInvariant()
$services = @($plan.services | ForEach-Object { [string]$_ } | Select-Object -Unique)
$allowedPaths = @($plan.allowed_paths | ForEach-Object { [string]$_ })
$endpoints = @($plan.endpoints)
$protectedServices = @("CollectorService", "CTIP-SMS", "CTIP-FormsPublic") | Where-Object {
    $services -notcontains $_
}

Import-CtipRuntimeEnvironment -InstallDir $InstallDir -ServiceName "CTIP-Web" | Out-Null
Set-Location $InstallDir

$currentCommit = Get-GitText -Arguments @("-C", $InstallDir, "rev-parse", "HEAD") -Label "Odczyt HEAD produkcji"
if ($currentCommit.ToLowerInvariant() -ne $expectedCurrent) {
    throw "Produkcja ma commit $currentCommit, oczekiwano $expectedCurrent."
}
$dirty = Get-GitText -Arguments @("-C", $InstallDir, "status", "--porcelain") -Label "Kontrola czystości Git"
if ($dirty) {
    throw "Produkcyjny katalog Git zawiera lokalne zmiany."
}
Assert-AlembicRevision -Expected $alembicBefore -WorkingDirectory $InstallDir -Label "Bieżąca rewizja Alembic"
foreach ($serviceName in @($services + $protectedServices | Select-Object -Unique)) {
    Assert-ServiceRunning -Name $serviceName
}
Assert-Endpoints -Endpoints $endpoints

if (-not $Apply) {
    Write-CtipStatus -Level "OK" -Message "Dry-run zakończony bez fetch, backupu, migracji i restartu usług."
    [pscustomobject]@{
        mode = "dry-run"
        current_commit = $currentCommit
        release = $release
        alembic = $alembicBefore
        services = $services
    } | ConvertTo-Json -Compress
    exit 0
}

$candidateRoot = Join-Path $InstallDir ".deploy\candidates"
$candidatePath = Join-Path $candidateRoot $release.Substring(0, 12)
$backupScript = Join-Path $InstallDir "scripts\windows\backup_prod_databases.ps1"
$stoppedServices = @()
$releaseCheckedOut = $false
$candidateCreated = $false

try {
    New-Item -ItemType Directory -Path $candidateRoot -Force | Out-Null
    Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "fetch", "--prune", "origin") -Label "Pobranie obiektów release" | Out-Null
    Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "cat-file", "-e", "$release`^{commit}") -Label "Kontrola obiektu release" | Out-Null
    Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "merge-base", "--is-ancestor", $expectedCurrent, $release) -Label "Kontrola pochodzenia release" | Out-Null
    Assert-AllowedChanges -CurrentCommit $expectedCurrent -ReleaseCommit $release -AllowedPaths $allowedPaths

    if (-not (Test-Path -LiteralPath $backupScript)) {
        throw "Brak skryptu pełnego backupu: $backupScript"
    }
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $backupScript -InstallDir $InstallDir
    if ($LASTEXITCODE -ne 0) {
        throw "Pełny backup PostgreSQL/Firebird zakończył się kodem $LASTEXITCODE."
    }

    if (Test-Path -LiteralPath $candidatePath) {
        throw "Katalog kandydata już istnieje: $candidatePath"
    }
    Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "worktree", "add", "--detach", $candidatePath, $release) -Label "Utworzenie worktree kandydata" | Out-Null
    $candidateCreated = $true

    $pythonPath = Join-Path $InstallDir ".venv\Scripts\python.exe"
    Invoke-CtipNative -FilePath $pythonPath -ArgumentList @("-m", "compileall", "-q", (Join-Path $candidatePath "app"), (Join-Path $candidatePath "scripts")) -Label "Compileall kandydata" | Out-Null
    $heads = @(Get-AlembicRevisions -Arguments @("heads") -WorkingDirectory $candidatePath -Label "Głowy Alembic kandydata")
    if ($heads.Count -ne 1 -or $heads[0] -ne $alembicAfter) {
        throw "Kandydat ma głowy Alembic $($heads -join ', '), oczekiwano $alembicAfter."
    }
    Assert-AlembicRevision -Expected $alembicBefore -WorkingDirectory $candidatePath -Label "Rewizja bazy przed migracją"
    Push-Location $candidatePath
    try {
        Invoke-CtipNative -FilePath $pythonPath -ArgumentList @("-m", "alembic", "upgrade", $alembicAfter) -Label "Migracja produkcyjnej bazy PostgreSQL" | Out-Null
    }
    finally {
        Pop-Location
    }
    Assert-AlembicRevision -Expected $alembicAfter -WorkingDirectory $candidatePath -Label "Rewizja bazy po migracji"

    foreach ($serviceName in $services) {
        Stop-Service -Name $serviceName -ErrorAction Stop
        $stoppedServices += $serviceName
    }
    Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "checkout", "--detach", $release) -Label "Przełączenie produkcji na release" | Out-Null
    $releaseCheckedOut = $true
    foreach ($serviceName in $services) {
        Start-Service -Name $serviceName -ErrorAction Stop
    }
    $stoppedServices = @()
    foreach ($serviceName in @($services + $protectedServices | Select-Object -Unique)) {
        Assert-ServiceRunning -Name $serviceName
    }
    Assert-Endpoints -Endpoints $endpoints
    if ($services -contains "CTIP-Web") {
        Assert-CurrentWebLog
    }
}
catch {
    Write-CtipStatus -Level "FAIL" -Message $_.Exception.Message
    if ($releaseCheckedOut) {
        foreach ($serviceName in $services) {
            Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
        }
        Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "checkout", "--detach", $expectedCurrent) -Label "Rollback kodu" -AllowFailure | Out-Null
    }
    foreach ($serviceName in @($services + $stoppedServices | Select-Object -Unique)) {
        Start-Service -Name $serviceName -ErrorAction SilentlyContinue
    }
    throw
}
finally {
    if ($candidateCreated) {
        Invoke-CtipNative -FilePath "git.exe" -ArgumentList @("-C", $InstallDir, "worktree", "remove", "--force", $candidatePath) -Label "Usunięcie tymczasowego worktree" -AllowFailure | Out-Null
    }
}

$rollbackServices = $services -join ","

[pscustomobject]@{
    mode = "apply"
    previous_commit = $expectedCurrent
    release = $release
    alembic = $alembicAfter
    restarted_services = $services
    protected_services = $protectedServices
    rollback = "git -C $InstallDir checkout --detach $expectedCurrent; Start-Service $rollbackServices"
} | ConvertTo-Json -Compress
