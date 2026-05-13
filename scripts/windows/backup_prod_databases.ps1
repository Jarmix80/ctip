param(
    [string]$InstallDir = "D:\CTIP",
    [string]$EnvFile = ".env",
    [string]$BackupRoot = "D:\CTIP\backups",
    [string]$PgDumpPath = "",
    [string]$PgDumpAllPath = "",
    [string]$GbakPath = "",
    [switch]$SkipPgGlobals,
    [switch]$FailOnPgGlobalsError,
    [switch]$AllowTestDatabase
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
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Fail "Uruchom PowerShell w trybie Administratora."
    }
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Fail "Brak pliku srodowiskowego: $Path"
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        if ($line.StartsWith("#")) { return }
        $parts = $line -split "=", 2
        if ($parts.Count -lt 2) { return }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.StartsWith('"') -and $value.EndsWith('"')) { $value = $value.Trim('"') }
        if ($value.StartsWith("'") -and $value.EndsWith("'")) { $value = $value.Trim("'") }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Resolve-Executable {
    param(
        [string]$PreferredPath,
        [string]$CommandName,
        [string[]]$Candidates
    )

    if ($PreferredPath) {
        if (Test-Path $PreferredPath) {
            return (Resolve-Path $PreferredPath).Path
        }
        Fail "Nie znaleziono pliku: $PreferredPath"
    }

    $fromPath = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($fromPath -and (Test-Path $fromPath.Source)) {
        return $fromPath.Source
    }

    foreach ($item in $Candidates) {
        if ($item -and (Test-Path $item)) {
            return (Resolve-Path $item).Path
        }
    }

    Fail "Nie znaleziono $CommandName. Podaj sciezke parametrem."
}

function Invoke-Native {
    param(
        [string]$Executable,
        [string[]]$Args,
        [string]$Label,
        [switch]$IgnoreErrors
    )

    $tempStdout = [System.IO.Path]::GetTempFileName()
    $tempStderr = [System.IO.Path]::GetTempFileName()
    try {
        Write-Log "$Label"
        $process = Start-Process `
            -FilePath $Executable `
            -ArgumentList $Args `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $tempStdout `
            -RedirectStandardError $tempStderr

        $stdout = Get-Content -Path $tempStdout -ErrorAction SilentlyContinue
        $stderr = Get-Content -Path $tempStderr -ErrorAction SilentlyContinue

        if ($stdout) { $stdout | ForEach-Object { Write-Host $_ } }
        if ($stderr) { $stderr | ForEach-Object { Write-Host $_ } }

        if ($process.ExitCode -ne 0 -and -not $IgnoreErrors) {
            Fail "$Label zakonczone bledem (exit=$($process.ExitCode))."
        }
        return $process.ExitCode
    } finally {
        Remove-Item -Path $tempStdout -ErrorAction SilentlyContinue
        Remove-Item -Path $tempStderr -ErrorAction SilentlyContinue
    }
}

function Require-Env {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        Fail "Brak zmiennej $Name w pliku srodowiskowym."
    }
}

function Sanitize-Name {
    param([string]$Value)
    $clean = ($Value -replace '[^A-Za-z0-9._-]+', "_").Trim("_")
    if (-not $clean) { return "unknown" }
    return $clean
}

function Resolve-FirebirdDsn {
    param(
        [string]$Database,
        [string]$Host,
        [string]$Port
    )

    if ([string]::IsNullOrWhiteSpace($Database)) {
        Fail "Brak FB_DATABASE."
    }

    $isWindowsPath = $Database -match '^[A-Za-z]:[\\/]'
    if ($isWindowsPath) {
        if ([string]::IsNullOrWhiteSpace($Host)) {
            return $Database
        }
        if ([string]::IsNullOrWhiteSpace($Port)) {
            return "$Host`:$Database"
        }
        return "$Host/$Port`:$Database"
    }

    return $Database
}

Assert-Admin

if (-not (Test-Path $InstallDir)) {
    Fail "Katalog nie istnieje: $InstallDir"
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

$envPath = Join-Path $InstallDir $EnvFile
Import-DotEnv -Path $envPath

Require-Env -Name "PGHOST"
Require-Env -Name "PGPORT"
Require-Env -Name "PGDATABASE"
Require-Env -Name "PGUSER"
Require-Env -Name "FB_DATABASE"
Require-Env -Name "FB_USER"
Require-Env -Name "FB_PASSWORD"

if (-not $AllowTestDatabase -and $env:PGDATABASE -eq "ctip_test") {
    Fail "PGDATABASE=ctip_test. To wyglada na test, nie produkcje. Uzyj -AllowTestDatabase jesli to celowe."
}

$postgresBinFromProcess = ""
$postgresProcess = Get-Process -Name postgres -ErrorAction SilentlyContinue | Select-Object -First 1
if ($postgresProcess -and $postgresProcess.Path) {
    $postgresBinFromProcess = Split-Path $postgresProcess.Path
}

$pgDumpCandidates = @(
    (if ($postgresBinFromProcess) { Join-Path $postgresBinFromProcess "pg_dump.exe" } else { $null }),
    "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
    "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
    "C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
    "C:\Program Files\PostgreSQL\14\bin\pg_dump.exe"
)

$pgDumpAllCandidates = @(
    (if ($postgresBinFromProcess) { Join-Path $postgresBinFromProcess "pg_dumpall.exe" } else { $null }),
    "C:\Program Files\PostgreSQL\17\bin\pg_dumpall.exe",
    "C:\Program Files\PostgreSQL\16\bin\pg_dumpall.exe",
    "C:\Program Files\PostgreSQL\15\bin\pg_dumpall.exe",
    "C:\Program Files\PostgreSQL\14\bin\pg_dumpall.exe"
)

$gbakCandidates = @(
    "C:\Program Files\Firebird\Firebird_5_0\gbak.exe",
    "C:\Program Files\Firebird\Firebird_4_0\gbak.exe",
    "C:\Program Files\Firebird\Firebird_3_0\gbak.exe",
    "C:\Program Files\Firebird\Firebird_2_5\bin\gbak.exe"
)

$pgDumpExe = Resolve-Executable -PreferredPath $PgDumpPath -CommandName "pg_dump.exe" -Candidates $pgDumpCandidates
$pgDumpAllExe = Resolve-Executable -PreferredPath $PgDumpAllPath -CommandName "pg_dumpall.exe" -Candidates $pgDumpAllCandidates
$gbakExe = Resolve-Executable -PreferredPath $GbakPath -CommandName "gbak.exe" -Candidates $gbakCandidates

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $BackupRoot "prod_$stamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$safePgDb = Sanitize-Name $env:PGDATABASE
$safePgHost = Sanitize-Name $env:PGHOST
$safeFbDb = Sanitize-Name (Split-Path -Leaf $env:FB_DATABASE)

$pgDumpFile = Join-Path $backupDir "postgres_${safePgDb}_${safePgHost}_$stamp.dump"
$pgGlobalsFile = Join-Path $backupDir "postgres_globals_${safePgHost}_$stamp.sql"
$fbBackupFile = Join-Path $backupDir "firebird_${safeFbDb}_$stamp.fbk"

$fbDsn = Resolve-FirebirdDsn -Database $env:FB_DATABASE -Host $env:FB_HOST -Port $env:FB_PORT

Write-Log "Katalog backupu: $backupDir"
Write-Log "pg_dump.exe    : $pgDumpExe"
Write-Log "pg_dumpall.exe : $pgDumpAllExe"
Write-Log "gbak.exe       : $gbakExe"
Write-Log "FB DSN         : $fbDsn"

$pgDumpArgs = @(
    "-h", $env:PGHOST,
    "-p", $env:PGPORT,
    "-U", $env:PGUSER,
    "-d", $env:PGDATABASE,
    "-F", "c",
    "--no-owner",
    "--no-privileges",
    "-f", $pgDumpFile
)
Invoke-Native -Executable $pgDumpExe -Args $pgDumpArgs -Label "Backup PostgreSQL (pg_dump)"

if (-not $SkipPgGlobals) {
    $pgDumpAllArgs = @(
        "-h", $env:PGHOST,
        "-p", $env:PGPORT,
        "-U", $env:PGUSER,
        "--globals-only",
        "--no-role-passwords",
        "-f", $pgGlobalsFile
    )

    $globalsExit = Invoke-Native `
        -Executable $pgDumpAllExe `
        -Args $pgDumpAllArgs `
        -Label "Backup globalnych obiektow PostgreSQL (pg_dumpall --globals-only --no-role-passwords)" `
        -IgnoreErrors

    if ($globalsExit -ne 0) {
        if ($FailOnPgGlobalsError) {
            Fail "Backup globalnych obiektow PostgreSQL zakonczony bledem."
        }
        Write-Warning "Backup globalnych obiektow PostgreSQL nieudany. Kontynuuje, bo nie ustawiono -FailOnPgGlobalsError."
    }
}

$gbakArgs = @(
    "-b",
    "-v",
    "-user", $env:FB_USER,
    "-password", $env:FB_PASSWORD,
    $fbDsn,
    $fbBackupFile
)
Invoke-Native -Executable $gbakExe -Args $gbakArgs -Label "Backup Firebird (gbak)"

Write-Host ""
Write-Host "Backup zakonczony powodzeniem:"
Write-Host "  PostgreSQL dump   : $pgDumpFile"
if (-not $SkipPgGlobals) {
    Write-Host "  PostgreSQL globals: $pgGlobalsFile"
}
Write-Host "  Firebird gbak     : $fbBackupFile"
