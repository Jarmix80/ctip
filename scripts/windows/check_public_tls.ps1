param(
    [string]$HostName = "form.ksero-partner.com.pl",
    [int]$WarningDays = 21,
    [int]$CriticalDays = 7
)

$ErrorActionPreference = "Stop"

function Write-TlsEvent {
    param(
        [ValidateSet("INFORMATION", "WARNING", "ERROR")]
        [string]$Type,
        [int]$EventId,
        [string]$Message
    )

    & eventcreate.exe /L APPLICATION /SO "CTIP-TLS" /T $Type /ID $EventId /D $Message *> $null
}

$tcpClient = $null
$sslStream = $null

try {
    $tcpClient = [System.Net.Sockets.TcpClient]::new()
    $connectTask = $tcpClient.ConnectAsync($HostName, 443)
    if (-not $connectTask.Wait([TimeSpan]::FromSeconds(10))) {
        throw "Przekroczono czas połączenia TCP z ${HostName}:443."
    }

    $sslStream = [System.Net.Security.SslStream]::new($tcpClient.GetStream(), $false)
    $sslStream.AuthenticateAsClient($HostName)
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $sslStream.RemoteCertificate
    )
    $nowUtc = [DateTime]::UtcNow
    $expiresUtc = $certificate.NotAfter.ToUniversalTime()
    $daysLeft = [Math]::Floor(($expiresUtc - $nowUtc).TotalDays)
    $message = (
        "TLS host={0}; subject={1}; thumbprint={2}; expires_utc={3:o}; days_left={4}" -f
        $HostName,
        $certificate.Subject,
        $certificate.Thumbprint,
        $expiresUtc,
        $daysLeft
    )

    if ($daysLeft -le $CriticalDays) {
        Write-TlsEvent -Type "ERROR" -EventId 922 -Message $message
        Write-Error $message
        exit 2
    }
    if ($daysLeft -le $WarningDays) {
        Write-TlsEvent -Type "WARNING" -EventId 921 -Message $message
        Write-Warning $message
        exit 1
    }

    Write-Output $message
    exit 0
}
catch {
    $message = "Kontrola TLS hosta $HostName nie powiodła się: $($_.Exception.Message)"
    try {
        Write-TlsEvent -Type "ERROR" -EventId 923 -Message $message
    }
    catch {
        Write-Warning "Nie udało się zapisać zdarzenia CTIP-TLS w dzienniku Application."
    }
    Write-Error $message
    exit 2
}
finally {
    if ($null -ne $sslStream) {
        $sslStream.Dispose()
    }
    if ($null -ne $tcpClient) {
        $tcpClient.Dispose()
    }
}
