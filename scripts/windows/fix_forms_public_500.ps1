param(
    [string]$InstallDir = "D:\CTIP",
    [string]$ServiceName = "CTIP-FormsPublic",
    [string]$PublicHost = "form.ksero-partner.com.pl",
    [string]$FormsPgHost = "127.0.0.1",
    [string]$Token = "",
    [switch]$Apply,
    [switch]$SkipGitPull,
    [switch]$SkipEditableInstall,
    [int]$HealthTimeoutSec = 15
)

$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Write-Section {
    param([string]$Title)
    Write-Host "`n===== $Title ====="
}

function Assert-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Uruchom PowerShell w trybie Administratora."
    }
}

function Resolve-Nssm {
    $guess = "C:\Program Files\nssm\nssm.exe"
    if (Test-Path $guess) {
        return (Resolve-Path $guess).Path
    }
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Get-RegistryNssmValue {
    param(
        [string]$Name,
        [string]$ValueName
    )
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$Name\Parameters"
    if (-not (Test-Path $regPath)) {
        return $null
    }
    try {
        return (Get-ItemProperty -Path $regPath -Name $ValueName -ErrorAction Stop).$ValueName
    } catch {
        return $null
    }
}

function Get-NssmValue {
    param(
        [string]$Nssm,
        [string]$Name,
        [string]$Param
    )
    if ($Nssm) {
        $raw = (& $Nssm get $Name $Param 2>$null | Out-String).Trim()
        if (-not [string]::IsNullOrWhiteSpace($raw)) {
            return $raw
        }
    }

    switch ($Param) {
        "AppDirectory" { return (Get-RegistryNssmValue -Name $Name -ValueName "AppDirectory") }
        "Application" { return (Get-RegistryNssmValue -Name $Name -ValueName "Application") }
        "AppParameters" { return (Get-RegistryNssmValue -Name $Name -ValueName "AppParameters") }
        "AppStdout" { return (Get-RegistryNssmValue -Name $Name -ValueName "AppStdout") }
        "AppStderr" { return (Get-RegistryNssmValue -Name $Name -ValueName "AppStderr") }
        "AppEnvironmentExtra" { return (Get-RegistryNssmValue -Name $Name -ValueName "AppEnvironmentExtra") }
        default { return $null }
    }
}

function Convert-EnvExtraToLines {
    param([object]$Value)

    if ($null -eq $Value) {
        return @()
    }
    if ($Value -is [System.Array]) {
        return @($Value | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    $text = ([string]$Value) -replace "`0", "`n"
    return @($text -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Mask-EnvLines {
    param([string[]]$Lines)

    return @($Lines | ForEach-Object {
        if ($_ -match '^(PGPASSWORD=|.*PASSWORD=|.*SECRET=|.*TOKEN=)') {
            $key = ($_ -split "=", 2)[0]
            "$key=***MASKED***"
        } else {
            $_
        }
    })
}

function Read-DotEnvValues {
    param(
        [string]$EnvPath,
        [string[]]$Keys
    )

    if (-not (Test-Path $EnvPath)) {
        throw "Brak pliku srodowiskowego: $EnvPath"
    }

    $values = @{}
    Get-Content $EnvPath | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') {
            return
        }
        $parts = $_ -split "=", 2
        $key = $parts[0].Trim()
        if ($Keys -contains $key) {
            $values[$key] = $parts[1]
        }
    }
    return $values
}

function Merge-PgEnvironmentExtra {
    param(
        [string[]]$ExistingLines,
        [hashtable]$PgValues,
        [string[]]$Keys
    )

    $merged = @()
    foreach ($line in $ExistingLines) {
        $name = ($line -split "=", 2)[0].Trim()
        if ($Keys -notcontains $name) {
            $merged += $line
        }
    }
    foreach ($key in $Keys) {
        if (-not $PgValues.ContainsKey($key)) {
            throw "Brak $key w .env - nie moge ustawic AppEnvironmentExtra."
        }
        $merged += "$key=$($PgValues[$key])"
    }
    return @($merged)
}

function Set-ServiceEnvironmentExtra {
    param(
        [string]$Name,
        [string[]]$Lines
    )

    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$Name\Parameters"
    if (-not (Test-Path $regPath)) {
        throw "Brak konfiguracji rejestru NSSM: $regPath"
    }
    Set-ItemProperty -Path $regPath -Name AppEnvironmentExtra -Value ([string[]]$Lines)
}

function Invoke-HttpDiagnostic {
    param(
        [string]$Label,
        [string]$Url,
        [int[]]$ExpectedStatus = @(200),
        [hashtable]$Headers = @{},
        [int]$TimeoutSec = 12
    )

    $statusCode = 0
    $message = ""
    try {
        $response = Invoke-WebRequest -Uri $Url -Headers $Headers -Method GET -TimeoutSec $TimeoutSec -UseBasicParsing -MaximumRedirection 0
        $statusCode = [int]$response.StatusCode
    } catch {
        $message = $_.Exception.Message
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            try {
                $statusCode = [int]$_.Exception.Response.StatusCode.value__
            } catch {
                $statusCode = 0
            }
        }
    }

    [PSCustomObject]@{
        Label = $Label
        Url = $Url
        StatusCode = $statusCode
        ExpectedStatus = ($ExpectedStatus -join ",")
        IsExpected = ($ExpectedStatus -contains $statusCode)
        Error = $message
    }
}

function Test-FormsEndpoints {
    param(
        [string]$Host,
        [string]$OneTimeToken,
        [int]$TimeoutSec
    )

    $checks = @()
    $checks += Invoke-HttpDiagnostic -Label "public_health" -Url "https://$Host/health" -ExpectedStatus @(200) -TimeoutSec $TimeoutSec
    $checks += Invoke-HttpDiagnostic -Label "public_invalid_token" -Url "https://$Host/formularz/abc" -ExpectedStatus @(404,410) -TimeoutSec $TimeoutSec
    if (-not [string]::IsNullOrWhiteSpace($OneTimeToken)) {
        $checks += Invoke-HttpDiagnostic -Label "public_real_token" -Url "https://$Host/formularz/$OneTimeToken" -ExpectedStatus @(200,404,410) -TimeoutSec $TimeoutSec
    }
    $checks += Invoke-HttpDiagnostic -Label "local_public_health" -Url "http://127.0.0.1:8100/health" -ExpectedStatus @(200) -TimeoutSec $TimeoutSec
    $checks += Invoke-HttpDiagnostic -Label "local_public_invalid_token" -Url "http://127.0.0.1:8100/formularz/abc" -ExpectedStatus @(404,410) -TimeoutSec $TimeoutSec
    return ,$checks
}

function Show-Checks {
    param([object[]]$Checks)
    $Checks | ForEach-Object {
        $statusText = if ($_.StatusCode -gt 0) { $_.StatusCode } else { "ERR" }
        $okText = if ($_.IsExpected) { "OK" } else { "FAIL" }
        Write-Host ("[{0}] {1} -> {2} (oczekiwano: {3})" -f $okText, $_.Label, $statusText, $_.ExpectedStatus)
        if (-not $_.IsExpected -and $_.Error) {
            Write-Host ("    {0}" -f $_.Error)
        }
    }
}

function Invoke-GitPull {
    param(
        [string]$RepoDir
    )
    Write-Host "git fetch origin --tags"
    git -C $RepoDir fetch origin --tags
    if ($LASTEXITCODE -ne 0) {
        throw "git fetch nie powiodl sie."
    }

    Write-Host "git pull origin main --ff-only"
    git -C $RepoDir pull origin main --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull nie powiodl sie."
    }
}

Assert-Admin

if (-not (Test-Path $InstallDir)) {
    throw "Katalog $InstallDir nie istnieje."
}

$InstallDir = (Resolve-Path $InstallDir).Path
$nssm = Resolve-Nssm
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $service) {
    throw "Brak uslugi $ServiceName."
}

Write-Section "Stan uslugi"
Write-Host "Service: $ServiceName"
Write-Host "Status : $($service.Status)"
if ($nssm) {
    Write-Host "NSSM   : $nssm"
} else {
    Write-Host "NSSM   : nie znaleziono (fallback: rejestr)"
}

$appDir = Get-NssmValue -Nssm $nssm -Name $ServiceName -Param "AppDirectory"
$appExe = Get-NssmValue -Nssm $nssm -Name $ServiceName -Param "Application"
$appArgs = Get-NssmValue -Nssm $nssm -Name $ServiceName -Param "AppParameters"
$appStdout = Get-NssmValue -Nssm $nssm -Name $ServiceName -Param "AppStdout"
$appStderr = Get-NssmValue -Nssm $nssm -Name $ServiceName -Param "AppStderr"
$appEnvExtra = Get-NssmValue -Nssm $nssm -Name $ServiceName -Param "AppEnvironmentExtra"
$appEnvExtraLines = Convert-EnvExtraToLines -Value $appEnvExtra

Write-Section "Konfiguracja procesu"
Write-Host "AppDirectory : $appDir"
Write-Host "Application  : $appExe"
Write-Host "AppParameters: $appArgs"
Write-Host "AppStdout    : $appStdout"
Write-Host "AppStderr    : $appStderr"
Write-Host "AppEnvironmentExtra (masked):"
Mask-EnvLines -Lines $appEnvExtraLines | ForEach-Object { Write-Host ("  {0}" -f $_) }

Write-Section "Testy przed zmianami"
$before = Test-FormsEndpoints -Host $PublicHost -OneTimeToken $Token -TimeoutSec $HealthTimeoutSec
Show-Checks -Checks $before

if (-not $Apply) {
    Write-Section "Tryb diagnostyczny"
    Write-Host "Uruchom z -Apply aby wykonac naprawe (git pull, AppDirectory, AppEnvironmentExtra PG*, restart uslugi)."
    exit 0
}

Write-Section "Naprawa"
Set-Location $InstallDir

if (-not $SkipGitPull) {
    Invoke-GitPull -RepoDir $InstallDir
} else {
    Write-Host "Pomijam git pull (SkipGitPull)."
}

if (-not $SkipEditableInstall) {
    $venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Brak interpretera .venv: $venvPython"
    }
    Write-Host "python -m pip install -e ."
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        throw "pip install -e . nie powiodlo sie."
    }
} else {
    Write-Host "Pomijam pip install -e . (SkipEditableInstall)."
}

$pgKeys = @("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")
$envPath = Join-Path $InstallDir ".env"
$pgValues = Read-DotEnvValues -EnvPath $envPath -Keys $pgKeys
$pgValues["PGHOST"] = $FormsPgHost
$newEnvExtra = Merge-PgEnvironmentExtra -ExistingLines $appEnvExtraLines -PgValues $pgValues -Keys $pgKeys
Write-Host "Ustawiam AppEnvironmentExtra dla $ServiceName (PGHOST=$FormsPgHost, haslo zamaskowane)."
Set-ServiceEnvironmentExtra -Name $ServiceName -Lines $newEnvExtra
Write-Host "AppEnvironmentExtra po zmianie (masked):"
Mask-EnvLines -Lines $newEnvExtra | ForEach-Object { Write-Host ("  {0}" -f $_) }

if ($nssm) {
    if ($appDir -ne $InstallDir) {
        Write-Host "Korekta AppDirectory: $appDir -> $InstallDir"
        & $nssm set $ServiceName AppDirectory $InstallDir | Out-Null
    } else {
        Write-Host "AppDirectory jest poprawny."
    }
} else {
    Write-Warning "Nie znaleziono nssm.exe - nie moge automatycznie ustawic AppDirectory."
}

Write-Host "Restart uslugi $ServiceName"
Restart-Service -Name $ServiceName -Force -ErrorAction Stop
Start-Sleep -Seconds 3

Write-Section "Logi po restarcie"
if ($appStdout -and (Test-Path $appStdout)) {
    Write-Host "--- AppStdout (tail 80) ---"
    Get-Content -Tail 80 $appStdout
}
if ($appStderr -and (Test-Path $appStderr)) {
    Write-Host "--- AppStderr (tail 120) ---"
    Get-Content -Tail 120 $appStderr
}

Write-Section "Testy po zmianach"
$after = Test-FormsEndpoints -Host $PublicHost -OneTimeToken $Token -TimeoutSec $HealthTimeoutSec
Show-Checks -Checks $after

$has500 = $after | Where-Object { $_.StatusCode -eq 500 }
if ($has500) {
    Write-Warning "Nadal wystepuje HTTP 500. Przejrzyj AppStderr i sprawdz konfiguracje .env/.env.test dla uslugi publicznej."
    exit 2
}

Write-Section "Status koncowy"
Write-Host "Naprawa zakonczona. Jesli token jest aktywny, /formularz/{token} powinien dzialac bez 500."
