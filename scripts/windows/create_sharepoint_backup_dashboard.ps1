param(
    [Parameter(Mandatory = $true)]
    [string]$SiteUrl,

    [string]$LibraryTitle = "Backup_KP",
    [string]$PageName = "BackupKP-Dashboard",
    [switch]$OverwritePage,
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
        try {
            Connect-PnPOnline -Url $TargetSiteUrl -ClientId $AppClientId -ClientSecret $AppClientSecret -Tenant $TenantName -ErrorAction Stop
            Write-Host "[OK] App-only: polaczenie przez -ClientSecret."
        }
        catch {
            $directAuthError = $_.Exception.Message
            Write-Host "[WARN] App-only przez -ClientSecret nieudane: $directAuthError"
            Write-Host "[INFO] Proba awaryjna: token OAuth + -AccessToken"
            $token = Get-SharePointAccessToken -TenantName $TenantName -AppClientId $AppClientId -AppClientSecret $AppClientSecret -TargetSiteUrl $TargetSiteUrl
            try {
                Connect-PnPOnline -Url $TargetSiteUrl -AccessToken $token -ErrorAction Stop
                Write-Host "[OK] App-only: polaczenie przez -AccessToken."
            }
            catch {
                $tokenAuthError = $_.Exception.Message
                throw "Nie udalo sie zalogowac do SharePoint (app-only). Szczegoly: client-secret='$directAuthError'; access-token='$tokenAuthError'. Sprawdz uprawnienia aplikacji i admin consent."
            }
        }
    }
    else {
        Write-Host "[INFO] Tryb auth: device login"
        Connect-PnPOnline -Url $TargetSiteUrl -DeviceLogin -ClientId $AppClientId -Tenant $TenantName -ErrorAction Stop
        Write-Host "[OK] Device login zakonczony."
    }

    try {
        $web = Get-PnPWeb -Includes Title, Url -ErrorAction Stop
        Write-Host "[OK] Witryna: $($web.Title) [$($web.Url)]"
    }
    catch {
        throw "Autoryzacja zakonczona, ale brak dostepu do witryny: $($_.Exception.Message)"
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

function Ensure-ViewForFolder {
    param(
        [string]$ListTitle,
        [string]$ViewTitle,
        [string]$FolderServerRelativeUrl
    )

    $fields = @(
        "DocIcon",
        "LinkFilename",
        "File_x0020_Type",
        "FileSizeDisplay",
        "Modified",
        "Editor"
    )

    $escapedFolderPath = [System.Security.SecurityElement]::Escape($FolderServerRelativeUrl)

    $query = @"
<Where>
  <BeginsWith>
    <FieldRef Name='FileDirRef' />
    <Value Type='Text'>$escapedFolderPath</Value>
  </BeginsWith>
</Where>
<OrderBy>
  <FieldRef Name='Modified' Ascending='FALSE' />
</OrderBy>
"@

    $view = Get-PnPView -List $ListTitle | Where-Object { $_.Title -eq $ViewTitle } | Select-Object -First 1
    if (-not $view) {
        Write-Host "[INFO] Tworzenie widoku: $ViewTitle"
        $view = Add-PnPView -List $ListTitle -Title $ViewTitle -Fields $fields -Paged:$true -RowLimit 200
    }
    else {
        Write-Host "[INFO] Aktualizacja widoku: $ViewTitle"
    }

    Update-ViewDefinition -ListTitle $ListTitle -ViewId $view.Id -Fields $fields -ViewQuery $query -RowLimit 200 | Out-Null
    return (Get-PnPView -List $ListTitle -Identity $view.Id -ErrorAction Stop)
}

function Get-ViewUrl {
    param(
        [string]$Site,
        [string]$LibraryRootServerRelativeUrl,
        [Guid]$ViewId
    )
    $siteUri = [System.Uri]::new($Site)
    $serverPath = "{0}/Forms/AllItems.aspx" -f $LibraryRootServerRelativeUrl.TrimEnd("/")
    $encodedPath = "/" + ((($serverPath.TrimStart("/")) -split "/") | ForEach-Object {
            [System.Uri]::EscapeDataString($_)
        } | Where-Object { $_ -ne "" }) -join "/"

    return ("{0}://{1}{2}?viewid={3}" -f $siteUri.Scheme, $siteUri.Host, $encodedPath, $ViewId)
}

function Publish-DashboardPage {
    param(
        [string]$TargetPageName,
        [string]$TargetSiteUrl,
        [array]$Views,
        [switch]$ForceOverwrite
    )

    $pageIdentity = "$TargetPageName.aspx"
    $pageExists = Get-PnPPage -Identity $pageIdentity -ErrorAction SilentlyContinue
    if ($pageExists) {
        if (-not $ForceOverwrite) {
            throw "Strona $pageIdentity juz istnieje. Uruchom skrypt ponownie z -OverwritePage, aby odtworzyc zawartosc."
        }
        Write-Host "[INFO] Usuwanie istniejacej strony: $pageIdentity"
        Remove-PnPPage -Identity $pageIdentity -Force
    }

    Write-Host "[INFO] Tworzenie strony: $pageIdentity"
    Add-PnPPage -Name $TargetPageName -LayoutType Article | Out-Null

    Add-PnPPageSection -Page $pageIdentity -SectionTemplate OneColumn | Out-Null
    Add-PnPPageTextPart -Page $pageIdentity -Section 1 -Column 1 -Text @"
<h2>BackupKP - dashboard backupow</h2>
<p>Widok tylko do podgladu backupow pogrupowanych wg katalogow.</p>
"@ | Out-Null

    Add-PnPPageSection -Page $pageIdentity -SectionTemplate OneColumn | Out-Null

    $html = "<table><thead><tr><th>Kategoria</th><th>Sciezka</th><th>Widok tabeli</th></tr></thead><tbody>"
    foreach ($item in $Views) {
        $html += "<tr>"
        $html += "<td>$($item.Title)</td>"
        $html += "<td><code>$($item.Path)</code></td>"
        $html += "<td><a href='$($item.Url)' target='_blank' rel='noopener noreferrer'>Otworz tabele</a></td>"
        $html += "</tr>"
    }
    $html += "</tbody></table>"

    Add-PnPPageTextPart -Page $pageIdentity -Section 2 -Column 1 -Text $html | Out-Null
    Set-PnPPage -Identity $pageIdentity -Publish | Out-Null

    Write-Host "[OK] Strona opublikowana: $TargetSiteUrl/SitePages/$TargetPageName.aspx"
}

Ensure-Module -Name "PnP.PowerShell"

Write-Host "[INFO] Laczenie z SharePoint: $SiteUrl"
Connect-SharePoint -TargetSiteUrl $SiteUrl -TenantName $Tenant -AppClientId $ClientId -AppClientSecret $ClientSecret

$web = Get-PnPWeb
$list = Resolve-DocumentLibrary -PreferredTitle $LibraryTitle
$LibraryTitle = $list.Title
$list = Get-PnPList -Identity $LibraryTitle -Includes RootFolder -ErrorAction Stop
$libraryRoot = $list.RootFolder.ServerRelativeUrl.TrimEnd("/")

Write-Host "[OK] Biblioteka: $($list.Title)"
Write-Host "[OK] Root: $libraryRoot"

$folders = @(
    @{ Key = "CTIP"; Title = "BackupKP - CTIP"; Path = "$libraryRoot/BackupKP/CTIP" },
    @{ Key = "MS_PROD"; Title = "BackupKP - Menadzer Serwisu PROD"; Path = "$libraryRoot/BackupKP/Menadzer_Serwisu/prod" },
    @{ Key = "MS_TEST"; Title = "BackupKP - Menadzer Serwisu TEST"; Path = "$libraryRoot/BackupKP/Menadzer_Serwisu/test" },
    @{ Key = "OPTIMA"; Title = "BackupKP - Optima"; Path = "$libraryRoot/BackupKP/Optima" }
)

$createdViews = @()
foreach ($folder in $folders) {
    $view = Ensure-ViewForFolder -ListTitle $LibraryTitle -ViewTitle $folder.Title -FolderServerRelativeUrl $folder.Path
    $url = Get-ViewUrl -Site $SiteUrl -LibraryRootServerRelativeUrl $libraryRoot -ViewId $view.Id
    $createdViews += @{
        Title = $folder.Title
        Url = $url
        Path = $folder.Path
    }
}

Publish-DashboardPage -TargetPageName $PageName -TargetSiteUrl $SiteUrl -Views $createdViews -ForceOverwrite:$OverwritePage
Write-Host "[OK] Utworzone widoki tabelaryczne:"
foreach ($item in $createdViews) {
    Write-Host ("  - {0}: {1}" -f $item.Title, $item.Url)
}
