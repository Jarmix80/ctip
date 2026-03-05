param(
    [Parameter(Mandatory = $true)]
    [string]$SiteUrl,

    [string]$LibraryTitle = "Backup_KP",
    [string]$ViewName = "Backup Dashboard",
    [string]$Tenant = "kseropartner.onmicrosoft.com",
    [string]$ClientId = "31359c7f-bd7e-475c-86db-fdb8c937548e",
    [string]$ClientSecret = ""
)

$ErrorActionPreference = "Stop"

function Ensure-Module {
    param([string]$Name)
    if (-not (Get-Module -ListAvailable -Name $Name)) {
        Write-Host "[INFO] Instalacja modulu $Name..."
        Install-Module -Name $Name -Scope CurrentUser -Force -AllowClobber
    }
    Import-Module $Name -Force
}

function Get-SharePointAccessToken {
    param(
        [string]$TenantName,
        [string]$AppClientId,
        [string]$AppClientSecret,
        [string]$TargetSiteUrl
    )
    $siteUri = [System.Uri]::new($TargetSiteUrl)
    $scope = "https://$($siteUri.Host)/.default"
    $tokenUrl = "https://login.microsoftonline.com/$TenantName/oauth2/v2.0/token"
    $body = @{
        client_id = $AppClientId
        client_secret = $AppClientSecret
        scope = $scope
        grant_type = "client_credentials"
    }
    $resp = Invoke-RestMethod -Method Post -Uri $tokenUrl -ContentType "application/x-www-form-urlencoded" -Body $body
    if (-not $resp.access_token) {
        throw "Brak access_token w odpowiedzi OAuth."
    }
    return [string]$resp.access_token
}

function Connect-SharePoint {
    param(
        [string]$TargetSiteUrl,
        [string]$TenantName,
        [string]$AppClientId,
        [string]$AppClientSecret
    )

    if ($AppClientSecret -and $AppClientSecret.Trim()) {
        Write-Host "[INFO] Tryb auth: app-only (client secret)"
        $directAuthError = $null
        $directAccessError = $null
        try {
            Connect-PnPOnline -Url $TargetSiteUrl -ClientId $AppClientId -ClientSecret $AppClientSecret -Tenant $TenantName -ErrorAction Stop
            Write-Host "[OK] App-only: polaczenie przez -ClientSecret."
            try {
                $web = Get-PnPWeb -Includes Title, Url -ErrorAction Stop
                Write-Host "[OK] Witryna: $($web.Title) [$($web.Url)]"
                return
            }
            catch {
                $directAccessError = $_.Exception.Message
                Write-Host "[WARN] App-only przez -ClientSecret zestawione, ale brak dostepu do witryny: $directAccessError"
            }
        }
        catch {
            $directAuthError = $_.Exception.Message
            Write-Host "[WARN] App-only przez -ClientSecret nieudane: $directAuthError"
        }

        Write-Host "[INFO] Proba awaryjna: token OAuth + -AccessToken"
        $token = Get-SharePointAccessToken -TenantName $TenantName -AppClientId $AppClientId -AppClientSecret $AppClientSecret -TargetSiteUrl $TargetSiteUrl
        try {
            Connect-PnPOnline -Url $TargetSiteUrl -AccessToken $token -ErrorAction Stop
            Write-Host "[OK] App-only: polaczenie przez -AccessToken."
            $web = Get-PnPWeb -Includes Title, Url -ErrorAction Stop
            Write-Host "[OK] Witryna: $($web.Title) [$($web.Url)]"
            return
        }
        catch {
            $tokenAuthError = $_.Exception.Message
            throw "Nie udalo sie zalogowac do SharePoint (app-only). Szczegoly: client-secret='$directAuthError'; web-access='$directAccessError'; access-token='$tokenAuthError'. Sprawdz uprawnienia aplikacji i admin consent."
        }
    }
    else {
        Write-Host "[INFO] Tryb auth: device login"
        Connect-PnPOnline -Url $TargetSiteUrl -DeviceLogin -ClientId $AppClientId -Tenant $TenantName -ErrorAction Stop
        Write-Host "[OK] Device login zakonczony."
        try {
            $web = Get-PnPWeb -Includes Title, Url -ErrorAction Stop
            Write-Host "[OK] Witryna: $($web.Title) [$($web.Url)]"
        }
        catch {
            throw "Autoryzacja zakonczona, ale brak dostepu do witryny: $($_.Exception.Message)"
        }
    }
}

function Resolve-DocumentLibrary {
    param([string]$PreferredTitle)
    $candidates = @()
    if ($PreferredTitle) {
        $candidates += $PreferredTitle
    }
    $candidates += @("Backup_KP", "Documents", "Shared Documents", "Dokumenty")
    $candidates = $candidates | Select-Object -Unique

    foreach ($name in $candidates) {
        $list = Get-PnPList -Identity $name -ErrorAction SilentlyContinue
        if ($list) {
            return $list
        }
    }

    $all = Get-PnPList -ErrorAction SilentlyContinue
    $docLib = $all | Where-Object {
        $_.BaseTemplate -eq 101 -or $_.BaseType -eq "DocumentLibrary"
    } | Select-Object -First 1
    if ($docLib) {
        Write-Host "[WARN] Nie znaleziono wskazanej biblioteki. Uzyto pierwszej dostepnej: $($docLib.Title)"
        return $docLib
    }

    if ($PreferredTitle) {
        Write-Host "[WARN] Nie udalo sie pobrac list przez PnP. Uzycie wymuszonej biblioteki: $PreferredTitle"
        return [pscustomobject]@{ Title = $PreferredTitle }
    }

    Write-Host "[WARN] Nie udalo sie wykryc biblioteki. Uzycie domyslnej nazwy: Backup_KP"
    return [pscustomobject]@{ Title = "Backup_KP" }
}

function Update-ViewDefinition {
    param(
        [string]$ListTitle,
        [Guid]$ViewId,
        [string[]]$Fields,
        [string]$ViewQuery,
        [uint32]$RowLimit = 200,
        [bool]$MakeDefault = $false
    )

    if ($Fields -and $Fields.Count -gt 0) {
        Set-PnPView -List $ListTitle -Identity $ViewId -Fields $Fields | Out-Null
    }

    $viewRef = Get-PnPView -List $ListTitle -Identity $ViewId -ErrorAction Stop
    $viewRef.ViewQuery = $ViewQuery
    $viewRef.RowLimit = [uint32]$RowLimit
    $viewRef.Paged = $true
    if ($MakeDefault) {
        $viewRef.DefaultView = $true
    }

    $viewRef.Update()
    Invoke-PnPQuery
    return $viewRef
}

Ensure-Module -Name "PnP.PowerShell"

Write-Host "[INFO] Laczenie z SharePoint: $SiteUrl"
Connect-SharePoint -TargetSiteUrl $SiteUrl -TenantName $Tenant -AppClientId $ClientId -AppClientSecret $ClientSecret

$list = Resolve-DocumentLibrary -PreferredTitle $LibraryTitle
$LibraryTitle = $list.Title
Write-Host "[OK] Biblioteka: $($list.Title)"

$viewFields = @(
    "DocIcon",
    "LinkFilename",
    "File_x0020_Type",
    "FileSizeDisplay",
    "Modified",
    "Editor"
)

$view = Get-PnPView -List $LibraryTitle | Where-Object { $_.Title -eq $ViewName } | Select-Object -First 1
if (-not $view) {
    Write-Host "[INFO] Tworzenie widoku: $ViewName"
    $view = Add-PnPView -List $LibraryTitle -Title $ViewName -Fields $viewFields -Paged:$true -RowLimit 200
}
else {
    Write-Host "[INFO] Widok juz istnieje, aktualizacja ustawien: $ViewName"
}

$query = @"
<OrderBy>
  <FieldRef Name='Modified' Ascending='FALSE' />
</OrderBy>
<GroupBy Collapse='TRUE' GroupLimit='100'>
  <FieldRef Name='FileDirRef' />
</GroupBy>
"@

Update-ViewDefinition -ListTitle $LibraryTitle -ViewId $view.Id -Fields $viewFields -ViewQuery $query -RowLimit 200 -MakeDefault:$true | Out-Null

Write-Host "[OK] Widok '$ViewName' zostal ustawiony jako domyslny."
Write-Host "[OK] Uklad: grupowanie po folderach + sortowanie po dacie modyfikacji malejaco."
