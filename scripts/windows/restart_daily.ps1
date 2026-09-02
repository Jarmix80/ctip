param(
    [string]$InstallDir = "D:\\CTIP",
    [string[]]$ServiceNames = @("CollectorService", "CTIP-Web", "CTIP-SMS"),
    [string]$EnvFile = ".env",
    [string]$LogDir = "logs\\maintenance",
    [int]$StartDelaySec = 5,
    [switch]$SkipSmsAlert,
    [switch]$SkipEmailAlert
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

function New-LogFile {
    param([string]$BaseDir)
    if (-not (Test-Path $BaseDir)) {
        New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null
    }
    $stamp = (Get-Date).ToString("yyyy-MM-dd")
    return Join-Path $BaseDir ("daily_restart_{0}.log" -f $stamp)
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[{0}] [{1}] {2}" -f $ts, $Level, $Message
    Write-Host $line
    Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
}

function Test-ServiceState {
    param([string]$Name)
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service) {
        return "Usluga $Name nie istnieje."
    }
    if ($service.Status -ne "Running") {
        return "Usluga $Name nie jest uruchomiona (status: $($service.Status))."
    }
    return $null
}

function Test-CtipPort {
    if (-not $env:PBX_HOST -or -not $env:PBX_PORT) {
        return "Brak PBX_HOST lub PBX_PORT w .env."
    }
    if (Get-Command Test-NetConnection -ErrorAction SilentlyContinue) {
        $result = Test-NetConnection -ComputerName $env:PBX_HOST -Port $env:PBX_PORT -WarningAction SilentlyContinue
        if (-not $result.TcpTestSucceeded) {
            return "Brak polaczenia TCP do $($env:PBX_HOST):$($env:PBX_PORT)."
        }
        return $null
    }
    $pingOk = Test-Connection -ComputerName $env:PBX_HOST -Count 1 -Quiet
    if (-not $pingOk) {
        return "Brak odpowiedzi ICMP z $($env:PBX_HOST)."
    }
    return $null
}

function Test-DbConnection {
    $psqlPath = Resolve-PsqlPath
    if (-not $psqlPath) {
        return "Brak psql (ustaw PSQL_BIN lub dodaj do PATH)."
    }
    if (-not $env:PGHOST -or -not $env:PGPORT -or -not $env:PGDATABASE -or -not $env:PGUSER) {
        return "Brak danych PGHOST/PGPORT/PGDATABASE/PGUSER w .env."
    }
    $conn = "host=$($env:PGHOST) port=$($env:PGPORT) dbname=$($env:PGDATABASE) user=$($env:PGUSER)"
    if ($env:PGSSLMODE) {
        $conn = "$conn sslmode=$($env:PGSSLMODE)"
    }
    & $psqlPath "$conn" -v ON_ERROR_STOP=1 -Atc "select 1;" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        return "Test polaczenia z PostgreSQL nie powiodl sie."
    }
    return $null
}

function Resolve-PsqlPath {
    if ($env:PSQL_BIN -and (Test-Path $env:PSQL_BIN)) {
        return $env:PSQL_BIN
    }
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Send-SmsAlert {
    param([string]$Message)
    if ($SkipSmsAlert) {
        Write-Log "SMS alert pominiety (SkipSmsAlert)." "WARN"
        return
    }
    if (-not $env:ALERT_SMS_DEST) {
        Write-Log "Brak ALERT_SMS_DEST - SMS alert pominiety." "WARN"
        return
    }
    $psqlPath = Resolve-PsqlPath
    if (-not $psqlPath) {
        Write-Log "Brak psql (PSQL_BIN/PATH) - SMS alert pominiety." "WARN"
        return
    }
    if (-not $env:PGHOST -or -not $env:PGPORT -or -not $env:PGDATABASE -or -not $env:PGUSER) {
        Write-Log "Brak danych PG - SMS alert pominiety." "WARN"
        return
    }
    $destEsc = $env:ALERT_SMS_DEST.Replace("'", "''")
    $msgEsc = $Message.Replace("'", "''")
    $conn = "host=$($env:PGHOST) port=$($env:PGPORT) dbname=$($env:PGDATABASE) user=$($env:PGUSER)"
    if ($env:PGSSLMODE) {
        $conn = "$conn sslmode=$($env:PGSSLMODE)"
    }
    $sql = "set search_path=ctip; insert into sms_out(dest, text, source, status, origin, meta) " +
        "values ('$destEsc', '$msgEsc', 'system', 'NEW', 'system', jsonb_build_object('reason','daily_restart'));"
    & $psqlPath "$conn" -v ON_ERROR_STOP=1 -Atc $sql | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Nie udalo sie zapisac alertu SMS do kolejki." "WARN"
    } else {
        Write-Log "Alert SMS zapisany do kolejki." "INFO"
    }
}

function Send-EmailAlert {
    param([string]$Subject, [string]$Body)
    if ($SkipEmailAlert) {
        Write-Log "Email alert pominiety (SkipEmailAlert)." "WARN"
        return
    }
    if (-not $env:ALERT_EMAIL_TO) {
        Write-Log "Brak ALERT_EMAIL_TO - email alert pominiety." "WARN"
        return
    }
    if (-not $env:EMAIL_HOST) {
        Write-Log "Brak EMAIL_HOST - email alert pominiety." "WARN"
        return
    }
    $from = $env:EMAIL_SENDER_ADDRESS
    if (-not $from) {
        $from = $env:EMAIL_USERNAME
    }
    if (-not $from) {
        $from = "ctip@localhost"
    }
    $useSsl = $false
    if ($env:EMAIL_USE_SSL -eq "true" -or $env:EMAIL_USE_TLS -eq "true") {
        $useSsl = $true
    }
    $port = 587
    if ($env:EMAIL_PORT) {
        $port = [int]$env:EMAIL_PORT
    }
    $cred = $null
    if ($env:EMAIL_USERNAME -and $env:EMAIL_PASSWORD) {
        $secure = ConvertTo-SecureString -String $env:EMAIL_PASSWORD -AsPlainText -Force
        $cred = [PSCredential]::new($env:EMAIL_USERNAME, $secure)
    }
    $mailParams = @{
        To = $env:ALERT_EMAIL_TO
        From = $from
        SmtpServer = $env:EMAIL_HOST
        Port = $port
        Subject = $Subject
        Body = $Body
        UseSsl = $useSsl
        Credential = $cred
        ErrorAction = "Stop"
    }
    if ($env:EMAIL_REPLY_TO_ADDRESS) {
        $mailParams["ReplyTo"] = $env:EMAIL_REPLY_TO_ADDRESS
    }
    try {
        Send-MailMessage @mailParams
        Write-Log "Email alert wyslany." "INFO"
    } catch {
        Write-Log ("Email alert nieudany: {0}" -f $_.Exception.Message) "WARN"
    }
}

Assert-Admin

if (-not (Test-Path $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje."
}

$InstallDir = (Resolve-Path $InstallDir).Path
Set-Location $InstallDir

$envPath = Join-Path $InstallDir $EnvFile
Import-DotEnv -Path $envPath

$logDirPath = Join-Path $InstallDir $LogDir
$script:LogPath = New-LogFile -BaseDir $logDirPath

Write-Log "Start restartu dziennego uslug."

foreach ($name in $ServiceNames) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Log "Usluga $name nie istnieje." "WARN"
        continue
    }
    if ($service.Status -eq "Running") {
        Write-Log "Zatrzymywanie uslugi $name."
        Stop-Service -Name $name -Force -ErrorAction Stop
        Start-Sleep -Seconds 2
    }
}

foreach ($name in $ServiceNames) {
    Write-Log "Uruchamianie uslugi $name."
    Start-Service -Name $name -ErrorAction Stop
}

if ($StartDelaySec -gt 0) {
    Write-Log "Odczekaj $StartDelaySec s na stabilizacje."
    Start-Sleep -Seconds $StartDelaySec
}

$failures = @()

foreach ($name in $ServiceNames) {
    $problem = Test-ServiceState -Name $name
    if ($problem) {
        $failures += $problem
    }
}

$problem = Test-CtipPort
if ($problem) {
    $failures += $problem
}

$problem = Test-DbConnection
if ($problem) {
    $failures += $problem
}

if ($failures.Count -gt 0) {
    $summary = ($failures -join " | ")
    Write-Log ("Testy nie powiodly sie: {0}" -f $summary) "ERROR"
    $alertText = "CTIP: daily restart test failed: $summary"
    Send-SmsAlert -Message $alertText
    Send-EmailAlert -Subject "CTIP: blad testu po restarcie" -Body $summary
    exit 2
}

Write-Log "Testy zakonczone sukcesem."
exit 0
