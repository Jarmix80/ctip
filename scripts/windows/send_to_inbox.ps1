param(
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Source,
    [string]$ServerIp = "192.168.0.9",
    [string]$ServerUser = "marcin",
    [string]$RemoteRoot = "/home/marcin/projects/ctip/inbox",
    [int]$Port = 22
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Brak polecenia '$Name'. Zainstaluj OpenSSH Client w Windows."
    }
}

Test-Command -Name "ssh"
Test-Command -Name "scp"

$resolvedSources = @()
foreach ($item in $Source) {
    $resolved = Resolve-Path -LiteralPath $item -ErrorAction Stop
    foreach ($path in $resolved) {
        $resolvedSources += $path.Path
    }
}

if ($resolvedSources.Count -eq 0) {
    throw "Nie podano poprawnych plikow ani katalogow do wyslania."
}

$batchName = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$remoteTarget = "$RemoteRoot/$batchName"
$remoteLogin = "$ServerUser@$ServerIp"

Write-Host "Tworzenie katalogu docelowego na serwerze: $remoteTarget"
& ssh -p $Port $remoteLogin "mkdir -p '$remoteTarget'" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Nie udalo sie utworzyc katalogu docelowego na serwerze."
}

$destination = "${remoteLogin}:$remoteTarget/"

foreach ($localPath in $resolvedSources) {
    $scpArgs = @("-P", "$Port")
    if (Test-Path -LiteralPath $localPath -PathType Container) {
        $scpArgs += "-r"
    }
    $scpArgs += @($localPath, $destination)

    Write-Host "Kopiowanie: $localPath"
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Blad kopiowania dla: $localPath"
    }
}

Write-Host ""
Write-Host "Wysylka zakonczona sukcesem."
Write-Host "Katalog docelowy: $remoteTarget"
Write-Host "Podglad na serwerze: ls -la '$remoteTarget'"
