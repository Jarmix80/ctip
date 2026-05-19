param(
    [string]$InstallDir = "D:\\CTIP",
    [string[]]$ServiceNames = @("CollectorService", "CTIP-Web", "CTIP-SMS"),
    [switch]$IncludeFormsPublic,
    [int]$TailLines = 120
)

$ErrorActionPreference = "Stop"

if ($IncludeFormsPublic -and -not ($ServiceNames -contains "CTIP-FormsPublic")) {
    $ServiceNames += "CTIP-FormsPublic"
}

$script:Failures = @()
$script:Warnings = @()

function Write-Status {
    param(
        [string]$Level,
        [string]$Message
    )
    Write-Host ("[{0}] {1}" -f $Level, $Message)
}

function Add-Failure {
    param([string]$Message)
    $script:Failures += $Message
    Write-Status -Level "FAIL" -Message $Message
}

function Add-Warning {
    param([string]$Message)
    $script:Warnings += $Message
    Write-Status -Level "WARN" -Message $Message
}

function Add-Ok {
    param([string]$Message)
    Write-Status -Level "OK" -Message $Message
}

function Get-EnvMapFromFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) {
            return
        }
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        $value = $value.Trim("'").Trim('"')
        $map[$key] = $value
    }
    return $map
}

function Test-TcpPort {
    param(
        [string]$Host,
        [int]$Port,
        [int]$TimeoutMs = 3000
    )

    if (Get-Command Test-NetConnection -ErrorAction SilentlyContinue) {
        try {
            $result = Test-NetConnection -ComputerName $Host -Port $Port -WarningAction SilentlyContinue
            return [bool]$result.TcpTestSucceeded
        } catch {
            return $false
        }
    }

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($Host, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Check-ServiceStatus {
    param([string]$Name)

    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        Add-Failure "Brak uslugi $Name."
        return
    }

    if ($service.Status -ne "Running") {
        Add-Failure "Usluga $Name ma status $($service.Status) (oczekiwano Running)."
        return
    }

    Add-Ok "Usluga $Name dziala (StartType=$($service.StartType))."
}

function Show-Tail {
    param(
        [string]$Label,
        [string]$Path,
        [int]$Lines
    )

    if (-not (Test-Path $Path)) {
        Add-Warning "Brak pliku logu: $Path"
        return @()
    }

    $info = Get-Item $Path
    Add-Ok ("{0}: {1} (LastWrite={2})" -f $Label, $Path, $info.LastWriteTime)
    return @(Get-Content $Path -Tail $Lines)
}

function Check-LogPatterns {
    param(
        [string]$Label,
        [string[]]$Lines,
        [string[]]$FailPatterns,
        [string[]]$WarnPatterns
    )

    foreach ($pattern in $FailPatterns) {
        $hits = $Lines | Select-String -Pattern $pattern -SimpleMatch
        if ($hits) {
            Add-Failure ("{0}: wykryto wzorzec krytyczny '{1}'" -f $Label, $pattern)
            return
        }
    }

    foreach ($pattern in $WarnPatterns) {
        $hits = $Lines | Select-String -Pattern $pattern -SimpleMatch
        if ($hits) {
            Add-Warning ("{0}: wykryto ostrzezenie '{1}'" -f $Label, $pattern)
            return
        }
    }

    Add-Ok "$Label: brak wzorcow krytycznych w sprawdzanym ogonie logu."
}

if (-not (Test-Path $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje."
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

Write-Host "CTIP HEALTHCHECK"
Write-Host ("Host: {0}" -f $env:COMPUTERNAME)
Write-Host ("Czas: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Write-Host ("Katalog: {0}" -f $InstallDir)
Write-Host ""

Write-Host "=== Uslugi ==="
foreach ($serviceName in $ServiceNames) {
    Check-ServiceStatus -Name $serviceName
}
Write-Host ""

Write-Host "=== Konfiguracja .env ==="
$envPath = Join-Path $InstallDir ".env"
$envMap = Get-EnvMapFromFile -Path $envPath
if ($envMap.Count -eq 0) {
    Add-Warning "Nie udalo sie odczytac .env lub plik jest pusty."
} else {
    $pbxHost = $envMap["PBX_HOST"]
    $pbxPort = $envMap["PBX_PORT"]
    $pgHost = $envMap["PGHOST"]
    $pgPort = $envMap["PGPORT"]
    $pgDb = $envMap["PGDATABASE"]
    $smsMode = $envMap["SMS_TEST_MODE"]

    Add-Ok ("PBX={0}:{1}" -f $pbxHost, $pbxPort)
    Add-Ok ("PG={0}:{1}/{2}" -f $pgHost, $pgPort, $pgDb)
    Add-Ok ("SMS_TEST_MODE={0}" -f $smsMode)

    if ($pbxHost -and $pbxPort) {
        if (Test-TcpPort -Host $pbxHost -Port ([int]$pbxPort)) {
            Add-Ok "Polaczenie TCP do PBX dziala."
        } else {
            Add-Failure "Brak polaczenia TCP do PBX ($pbxHost`:$pbxPort)."
        }
    } else {
        Add-Warning "Brak PBX_HOST lub PBX_PORT w .env."
    }

    if ($pgHost -and $pgPort) {
        if (Test-TcpPort -Host $pgHost -Port ([int]$pgPort)) {
            Add-Ok "Polaczenie TCP do PostgreSQL dziala."
        } else {
            Add-Failure "Brak polaczenia TCP do PostgreSQL ($pgHost`:$pgPort)."
        }
    } else {
        Add-Warning "Brak PGHOST lub PGPORT w .env."
    }
}
Write-Host ""

Write-Host "=== Logi biezacego dnia ==="
$today = Get-Date -Format "yyyy-MM-dd"
$collectorDaily = Join-Path $InstallDir ("docs\\LOG\\Centralka\\log_collector_{0}.log" -f $today)
$smsDaily = Join-Path $InstallDir ("docs\\LOG\\sms\\sms_sender_{0}.log" -f $today)

$collectorLines = Show-Tail -Label "Collector daily" -Path $collectorDaily -Lines $TailLines
$smsLines = Show-Tail -Label "SMS daily" -Path $smsDaily -Lines $TailLines

if ($collectorLines.Count -gt 0) {
    Check-LogPatterns -Label "Collector daily" -Lines $collectorLines `
        -FailPatterns @(
            "LOGA odrzucone",
            "Brak potwierdzenia LOGA",
            "Niekompletna struktura bazy",
            "Blad po stronie PostgreSQL"
        ) `
        -WarnPatterns @(
            "Connection refused",
            "reconnect in"
        )
}

if ($smsLines.Count -gt 0) {
    Check-LogPatterns -Label "SMS daily" -Lines $smsLines `
        -FailPatterns @(
            "Blad polaczenia z PostgreSQL",
            "password authentication failed",
            "autoryzacja has"
        ) `
        -WarnPatterns @(
            "Blad transportu"
        )
}
Write-Host ""

Write-Host "=== Logi uslug (stderr) ==="
$collectorErr = Join-Path $InstallDir "logs\\collector\\collector_stderr.log"
$smsErr = Join-Path $InstallDir "logs\\sms\\sms_stderr.log"

$collectorErrLines = Show-Tail -Label "Collector stderr" -Path $collectorErr -Lines $TailLines
$smsErrLines = Show-Tail -Label "SMS stderr" -Path $smsErr -Lines $TailLines

if ($collectorErrLines.Count -gt 0) {
    $startMarkers = ($collectorErrLines | Select-String -Pattern "=== starting collector_full.py ===" -SimpleMatch).Count
    if ($startMarkers -ge 5) {
        Add-Warning "Collector stderr: wiele markerow startu - sprawdz czy nie ma petli restartow uslugi."
    }
}

if ($smsErrLines.Count -gt 0) {
    Check-LogPatterns -Label "SMS stderr" -Lines $smsErrLines `
        -FailPatterns @(
            "password authentication failed",
            "autoryzacja has"
        ) `
        -WarnPatterns @(
            "OperationalError"
        )
}
Write-Host ""

Write-Host "=== Podsumowanie ==="
if ($script:Warnings.Count -gt 0) {
    Write-Status -Level "WARN" -Message ("Liczba ostrzezen: {0}" -f $script:Warnings.Count)
}

if ($script:Failures.Count -gt 0) {
    Write-Status -Level "FAIL" -Message ("Liczba bledow krytycznych: {0}" -f $script:Failures.Count)
    exit 2
}

Write-Status -Level "OK" -Message "Healthcheck zakonczony sukcesem."
exit 0
