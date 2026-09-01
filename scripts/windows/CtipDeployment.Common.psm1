$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-CtipStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Level,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Host ("[{0}] [{1}] {2}" -f $stamp, $Level, $Message)
}

function Import-CtipDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)
    $count = 0
    if (-not (Test-Path -LiteralPath $Path)) {
        return $count
    }
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $line.Substring(0, $separator).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            continue
        }
        $value = $line.Substring($separator + 1).Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
        $count++
    }
    return $count
}

function Get-CtipNssmEnvironment {
    param([Parameter(Mandatory = $true)][string]$ServiceName)
    $result = @{}
    $registryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
    if (-not (Test-Path -LiteralPath $registryPath)) {
        return $result
    }
    $entries = @((Get-ItemProperty -LiteralPath $registryPath -Name AppEnvironmentExtra -ErrorAction SilentlyContinue).AppEnvironmentExtra)
    foreach ($entry in $entries) {
        if ([string]::IsNullOrWhiteSpace([string]$entry)) {
            continue
        }
        $separator = ([string]$entry).IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = ([string]$entry).Substring(0, $separator).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            continue
        }
        $result[$name] = ([string]$entry).Substring($separator + 1)
    }
    return $result
}

function Import-CtipRuntimeEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$InstallDir,
        [string]$ServiceName = "CTIP-Web"
    )
    $envPath = Join-Path $InstallDir ".env"
    $fileCount = Import-CtipDotEnv -Path $envPath
    $nssmEnvironment = Get-CtipNssmEnvironment -ServiceName $ServiceName
    foreach ($name in $nssmEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $nssmEnvironment[$name], "Process")
    }
    Write-CtipStatus -Level "OK" -Message (
        "Wczytano konfigurację procesu: .env={0}, NSSM={1}; wartości ukryte." -f
        $fileCount,
        $nssmEnvironment.Count
    )
    return [pscustomobject]@{
        DotEnvCount = $fileCount
        NssmCount = $nssmEnvironment.Count
    }
}

function Invoke-CtipNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$AllowFailure
    )
    Write-CtipStatus -Level "INFO" -Message $Label
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $FilePath @ArgumentList 2>&1)
        $nativeSucceeded = $?
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($null -eq $exitCode) {
        $exitCode = if ($nativeSucceeded) { 0 } else { 1 }
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $safeTail = @($output | Select-Object -Last 20) -join [Environment]::NewLine
        throw "$Label zakończyło się kodem $exitCode.`n$safeTail"
    }
    return [pscustomobject]@{
        ExitCode = [int]$exitCode
        Output = @($output | ForEach-Object { [string]$_ })
    }
}

function Get-CtipNormalizedSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $text = [IO.File]::ReadAllText($Path)
    $normalized = (($text -replace "`r`n", "`n") -replace "`r", "`n").TrimEnd("`n") + "`n"
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($normalized)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $hasher.ComputeHash($bytes)
    }
    finally {
        $hasher.Dispose()
    }
    return [BitConverter]::ToString($hash).Replace("-", "").ToLowerInvariant()
}

function Test-CtipHttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$ExpectedStatus = 200,
        [int]$Attempts = 20
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ([int]$response.StatusCode -eq $ExpectedStatus) {
                Write-CtipStatus -Level "OK" -Message "$Label zwrócił HTTP $ExpectedStatus."
                return
            }
        }
        catch {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq $ExpectedStatus) {
                Write-CtipStatus -Level "OK" -Message "$Label zwrócił HTTP $ExpectedStatus."
                return
            }
        }
        Start-Sleep -Seconds 3
    }
    throw "$Label nie zwrócił oczekiwanego HTTP ${ExpectedStatus}: $Url"
}

function Get-CtipServiceStartTime {
    param([Parameter(Mandatory = $true)][string]$ServiceName)
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if (-not $service -or -not $service.ProcessId) {
        return $null
    }
    return (Get-Process -Id $service.ProcessId -ErrorAction SilentlyContinue).StartTime
}

function Get-CtipCurrentStartLog {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ServiceName = "CTIP-Web",
        [int]$TailLines = 400
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }
    $lines = @(Get-Content -LiteralPath $Path -Tail $TailLines)
    $lastMarker = -1
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ([string]$lines[$index] -match 'Started server process') {
            $lastMarker = $index
        }
    }
    if ($lastMarker -ge 0) {
        return @($lines[$lastMarker..($lines.Count - 1)])
    }
    $startedAt = Get-CtipServiceStartTime -ServiceName $ServiceName
    if (-not $startedAt) {
        return @($lines | Select-Object -Last 120)
    }
    $filtered = @()
    foreach ($line in $lines) {
        if ([string]$line -match '^\[?(?<stamp>\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+)') {
            $parsed = [datetime]::MinValue
            if ([datetime]::TryParse($Matches.stamp, [ref]$parsed) -and $parsed -ge $startedAt) {
                $filtered += $line
            }
        }
    }
    return $filtered
}

Export-ModuleMember -Function @(
    "Write-CtipStatus",
    "Import-CtipDotEnv",
    "Get-CtipNssmEnvironment",
    "Import-CtipRuntimeEnvironment",
    "Invoke-CtipNative",
    "Get-CtipNormalizedSha256",
    "Test-CtipHttpEndpoint",
    "Get-CtipServiceStartTime",
    "Get-CtipCurrentStartLog"
)
