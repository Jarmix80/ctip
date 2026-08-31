param(
    [string]$RepoPath = "D:\CTIP",
    [string]$ExpectedCurrentCommit = "7ca3178e5295b6af52c7756f15d4e174bed38b31",
    [string]$ExpectedAlembicCurrent = "f9a0b1c2d3e4",
    [string]$ExpectedAlembicHead = "a7c4e2f9b1d3",
    [string]$ReleaseRef = "origin/feature/mailbox-archive-ledger-2026-08-28",
    [switch]$Apply
)

throw "Skrypt archiwalny jest zablokowany. Użyj scripts/deploy_windows_prod.py z pełnym SHA i aktualnym planem wydania."

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Description)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description zakończyło się kodem $LASTEXITCODE."
    }
}

Set-Location $RepoPath
$currentCommit = (git rev-parse HEAD).Trim()
$currentStatus = (git status --porcelain)
$currentAlembic = (& .\.venv\Scripts\python.exe -m alembic current 2>&1 | Out-String).Trim()

if ($currentCommit -ne $ExpectedCurrentCommit) {
    throw "Nieoczekiwany commit produkcji: $currentCommit."
}
if ($currentStatus) {
    throw "Katalog produkcyjny zawiera lokalne zmiany."
}
if ($currentAlembic -notmatch [regex]::Escape($ExpectedAlembicCurrent)) {
    throw "Nieoczekiwana rewizja Alembic: $currentAlembic."
}

$protectedServices = @("CTIP-FormsPublic", "CollectorService", "CTIP-SMS")
foreach ($serviceName in $protectedServices) {
    if ((Get-Service -Name $serviceName).Status -ne "Running") {
        throw "Usługa chroniona $serviceName nie działa przed wdrożeniem."
    }
}

$webServiceParameters = "HKLM:\SYSTEM\CurrentControlSet\Services\CTIP-Web\Parameters"
$environmentEntries = @((Get-ItemProperty $webServiceParameters).AppEnvironmentExtra)
$environmentWithoutMailboxFlag = @(
    $environmentEntries | Where-Object { $_ -notmatch '^CONTRACTS_MAILBOX_PROCESSING_ENABLED=' }
)

if (-not $Apply) {
    Write-Host "Kontrola wstępna zakończona. Użyj -Apply po wykonaniu kopii produkcyjnej."
    exit 0
}

Stop-Service -Name "CTIP-Web"
try {
    Set-ItemProperty -Path $webServiceParameters -Name AppEnvironmentExtra -Type MultiString -Value @(
        $environmentWithoutMailboxFlag + "CONTRACTS_MAILBOX_PROCESSING_ENABLED=false"
    )
    Invoke-Checked { git fetch origin } "Pobranie informacji o wydaniu"
    Invoke-Checked { git checkout --detach $ReleaseRef } "Przełączenie na zatwierdzone wydanie"
    Invoke-Checked { .\.venv\Scripts\python.exe -m alembic upgrade $ExpectedAlembicHead } "Migracja Alembic"
    $dryRunOutput = (& .\.venv\Scripts\python.exe scripts\contracts_mailbox_sync.py --backfill --dry-run 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Próbny backfill zakończył się kodem $LASTEXITCODE.`n$dryRunOutput"
    }
    $expectedCounters = @(
        "linked_form=118",
        "historical_archived=51",
        "ignored=2",
        "manual_hold=1",
        "error=0"
    )
    foreach ($expectedCounter in $expectedCounters) {
        if ($dryRunOutput -notmatch [regex]::Escape($expectedCounter)) {
            throw "Próbny backfill nie zawiera oczekiwanego licznika $expectedCounter."
        }
    }
    Invoke-Checked {
        .\.venv\Scripts\python.exe scripts\contracts_mailbox_sync.py --backfill --apply-backfill
    } "Zapis backfillu"
    Set-ItemProperty -Path $webServiceParameters -Name AppEnvironmentExtra -Type MultiString -Value @(
        $environmentWithoutMailboxFlag + "CONTRACTS_MAILBOX_PROCESSING_ENABLED=true"
    )
}
finally {
    Start-Service -Name "CTIP-Web"
}

foreach ($serviceName in @("CTIP-Web") + $protectedServices) {
    $service = Get-Service -Name $serviceName
    if ($service.Status -ne "Running") {
        throw "Usługa $serviceName nie działa po wdrożeniu."
    }
}

Write-Host "Migracja i kontrolowany backfill zostały zakończone."
