param(
    [string]$RootPath = "D:\Backup_CTIP_MS_optima"
)

$ErrorActionPreference = "Stop"

$folders = @(
    $RootPath,
    (Join-Path $RootPath "CTIP"),
    (Join-Path $RootPath "CTIP\files"),
    (Join-Path $RootPath "CTIP\db"),
    (Join-Path $RootPath "Menadzer_Serwisu"),
    (Join-Path $RootPath "Menadzer_Serwisu\prod"),
    (Join-Path $RootPath "Menadzer_Serwisu\test"),
    (Join-Path $RootPath "Optima"),
    (Join-Path $RootPath "logs")
)

foreach ($folder in $folders) {
    if (-not (Test-Path -LiteralPath $folder)) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
        Write-Host "[OK] Utworzono: $folder"
    }
    else {
        Write-Host "[SKIP] Istnieje: $folder"
    }
}

$readmePath = Join-Path $RootPath "_STRUKTURA_BACKUP.txt"
$readmeContent = @"
Struktura lokalnego backupu CTIP/MS/Optima

CTIP\files                 - kopie plikow aplikacji CTIP
CTIP\db                    - kopie bazy PostgreSQL CTIP
Menadzer_Serwisu\prod      - kopie produkcyjnej bazy Firebird
Menadzer_Serwisu\test      - kopie testowej bazy Firebird
Optima                      - kopie baz Comarch ERP Optima
logs                        - logi wykonania zadan backupu
"@

Set-Content -Path $readmePath -Value $readmeContent -Encoding UTF8
Write-Host "[OK] Zapisano opis struktury: $readmePath"
