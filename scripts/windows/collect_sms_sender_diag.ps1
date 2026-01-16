$ErrorActionPreference = "Continue"

function Write-Log {
    param([string]$Message)
    $Message | Add-Content -Path $script:OutFile -Encoding UTF8
}

function Get-EnvMapFromFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        $value = $value.Trim("'").Trim('"')
        $map[$key] = $value
    }
    return $map
}

function Mask-EnvLines {
    param([string[]]$Lines)
    $masked = @()
    foreach ($line in $Lines) {
        if ($line -match "^(.*?=)(.*)$") {
            $key = $matches[1]
            if ($key -match "(PASS|TOKEN|SECRET|KEY)") {
                $masked += ("{0}***" -f $key)
            } else {
                $masked += $line
            }
        } else {
            $masked += $line
        }
    }
    return $masked
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) {
    $scriptDir = (Get-Location).Path
}
$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$script:OutFile = Join-Path $scriptDir ("sms_sender_diag_{0}.log" -f $timestamp)

Write-Log "CTIP SMS DIAGNOSTYKA"
Write-Log ("Timestamp: {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Write-Log ("Host: {0}" -f $env:COMPUTERNAME)
Write-Log ("User: {0}" -f $env:USERNAME)
Write-Log ("ScriptDir: {0}" -f $scriptDir)
Write-Log ("WorkingDir: {0}" -f (Get-Location))
Write-Log ""

Write-Log "=== USLUGI ==="
try {
    $services = Get-Service -Name "CollectorService","CTIP-SMS","CTIP-Web" -ErrorAction SilentlyContinue |
        Select-Object Name, Status, StartType
    Write-Log ($services | Format-Table -AutoSize | Out-String)
} catch {
    Write-Log ("Blad odczytu uslug: {0}" -f $_.Exception.Message)
}
Write-Log ""

Write-Log "=== NSSM (CTIP-SMS) ==="
$nssmCandidates = @(
    "C:\Program Files\nssm-2.24\win64\nssm.exe",
    "C:\Program Files\nssm\win64\nssm.exe",
    "C:\Program Files\nssm\nssm.exe"
)
$nssmPath = $nssmCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $nssmPath) {
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) { $nssmPath = $cmd.Source }
}
if ($nssmPath) {
    Write-Log ("NSSM: {0}" -f $nssmPath)
    foreach ($param in @("Application","AppDirectory","AppParameters","AppEnvironmentExtra")) {
        $value = & $nssmPath get CTIP-SMS $param 2>&1
        if ($null -ne $value) {
            if ($value -is [System.Array]) {
                $value = ($value -join "`n")
            }
            $value = $value -replace "`0", ""
        }
        if ($param -eq "AppEnvironmentExtra") {
            $lines = ($value -split "`r?`n")
            $masked = Mask-EnvLines -Lines $lines
            Write-Log ("CTIP-SMS {0} (masked):" -f $param)
            Write-Log ($masked -join "`n")
        } else {
            Write-Log ("CTIP-SMS {0}: {1}" -f $param, $value)
        }
    }
} else {
    Write-Log "NSSM: nie znaleziono (PATH i domyslne lokalizacje)."
}
Write-Log ""

Write-Log "=== PROCESY PYTHON (sms_sender / collector) ==="
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*sms_sender.py*" -or $_.CommandLine -like "*collector_full.py*" } |
        Select-Object ProcessId, ParentProcessId, CommandLine
    Write-Log ($procs | Format-Table -AutoSize | Out-String)
    foreach ($proc in $procs) {
        $pp = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $proc.ParentProcessId) -ErrorAction SilentlyContinue
        if ($pp) {
            Write-Log ("Parent {0}: {1} {2}" -f $proc.ProcessId, $pp.Name, $pp.CommandLine)
        }
    }
} catch {
    Write-Log ("Blad odczytu procesow: {0}" -f $_.Exception.Message)
}
Write-Log ""

Write-Log "=== GIT (D:\\CTIP) ==="
try {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $head = & git -C "D:\CTIP" log -1 --oneline 2>&1
        $status = & git -C "D:\CTIP" status -sb 2>&1
        Write-Log ("HEAD: {0}" -f $head)
        Write-Log ("STATUS: {0}" -f $status)
    } else {
        Write-Log "git: brak w PATH"
    }
} catch {
    Write-Log ("Blad git: {0}" -f $_.Exception.Message)
}
Write-Log ""

Write-Log "=== ENV (.env) ==="
$envPath = "D:\CTIP\.env"
Write-Log ("Plik .env obecny: {0}" -f (Test-Path $envPath))
Write-Log ""

Write-Log "=== LOGI SMS ==="
$logDirs = @("D:\CTIP\docs\LOG\sms", "D:\CTIP\logs\sms")
foreach ($dir in $logDirs) {
    if (-not (Test-Path $dir)) {
        Write-Log ("Brak katalogu: {0}" -f $dir)
        continue
    }
    $latest = Get-ChildItem -Path $dir -Filter "sms_sender_*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($latest) {
        Write-Log ("Log: {0}" -f $latest.FullName)
        Write-Log ("Tail(200):")
        Get-Content $latest.FullName -Tail 200 | ForEach-Object { Write-Log $_ }
    } else {
        Write-Log ("Brak plikow sms_sender_*.log w {0}" -f $dir)
    }
}
Write-Log ""

Write-Log "=== KOLEJKA SMS (opcjonalnie, psql) ==="
try {
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if ($psql -and (Test-Path $envPath)) {
        $envMap = Get-EnvMapFromFile -Path $envPath
        foreach ($key in @("PGHOST","PGPORT","PGDATABASE","PGUSER","PGPASSWORD")) {
            if ($envMap.ContainsKey($key)) {
                [Environment]::SetEnvironmentVariable($key, $envMap[$key], "Process")
            }
        }
        $query = "SELECT status, count(*) FROM ctip.sms_out GROUP BY status ORDER BY status;"
        $out = & $psql -h $env:PGHOST -p $env:PGPORT -U $env:PGUSER -d $env:PGDATABASE -c $query 2>&1
        Write-Log $out
    } else {
        Write-Log "psql: brak w PATH lub .env nie istnieje - pomijam."
    }
} catch {
    Write-Log ("Blad psql: {0}" -f $_.Exception.Message)
}

Write-Log ""
Write-Log "KONIEC RAPORTU"
