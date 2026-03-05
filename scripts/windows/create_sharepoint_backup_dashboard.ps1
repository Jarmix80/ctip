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

    $query = @"
<Where>
  <Contains>
    <FieldRef Name='FileDirRef' />
    <Value Type='Text'>$FolderServerRelativeUrl</Value>
  </Contains>
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

    Set-PnPView -List $ListTitle -Identity $view.Id -Fields $fields -Values @{
        RowLimit = 200
        Paged = "TRUE"
        Query = $query
    }
    return $view
}

function Get-ViewUrl {
    param(
        [string]$Site,
        [string]$LibraryRootServerRelativeUrl,
        [Guid]$ViewId
    )
    $libPath = $LibraryRootServerRelativeUrl.TrimStart("/")
    return ("{0}/{1}/Forms/AllItems.aspx?viewid={2}" -f $Site.TrimEnd("/"), $libPath, $ViewId)
}

Ensure-Module -Name "PnP.PowerShell"

Write-Host "[INFO] Laczenie z SharePoint: $SiteUrl"
if ($ClientSecret -and $ClientSecret.Trim()) {
    Write-Host "[INFO] Tryb auth: app-only (client secret)"
    $token = Get-SharePointAccessToken -TenantName $Tenant -AppClientId $ClientId -AppClientSecret $ClientSecret -TargetSiteUrl $SiteUrl
    Connect-PnPOnline -Url $SiteUrl -AccessToken $token
}
else {
    Write-Host "[INFO] Tryb auth: device login"
    Connect-PnPOnline -Url $SiteUrl -DeviceLogin -ClientId $ClientId -Tenant $Tenant
}

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

$pageExists = Get-PnPPage -Identity "$PageName.aspx" -ErrorAction SilentlyContinue
if ($pageExists -and $OverwritePage) {
    Write-Host "[INFO] Usuwanie istniejacej strony: $PageName.aspx"
    Remove-PnPPage -Identity "$PageName.aspx" -Force
    $pageExists = $null
}

if (-not $pageExists) {
    Write-Host "[INFO] Tworzenie strony: $PageName.aspx"
    Add-PnPPage -Name $PageName -LayoutType Home | Out-Null
}
else {
    Write-Host "[INFO] Strona juz istnieje: $PageName.aspx"
}

$page = Get-PnPPage -Identity "$PageName.aspx"

# Czyszczenie sekcji i budowa od nowa
$page.Sections.Clear()

Add-PnPPageSection -Page $page -SectionTemplate OneColumn | Out-Null
Add-PnPPageTextPart -Page $page -Section 1 -Column 1 -Text @"
<h2>BackupKP - dashboard backupow</h2>
<p>Widok tylko do podgladu backupow pogrupowanych wg katalogow.</p>
"@ | Out-Null

Add-PnPPageSection -Page $page -SectionTemplate OneColumn | Out-Null

$html = "<table><thead><tr><th>Kategoria</th><th>Sciezka</th><th>Widok tabeli</th></tr></thead><tbody>"
foreach ($item in $createdViews) {
    $html += "<tr>"
    $html += "<td>$($item.Title)</td>"
    $html += "<td><code>$($item.Path)</code></td>"
    $html += "<td><a href='$($item.Url)' target='_blank' rel='noopener noreferrer'>Otworz tabele</a></td>"
    $html += "</tr>"
}
$html += "</tbody></table>"

Add-PnPPageTextPart -Page $page -Section 2 -Column 1 -Text $html | Out-Null

Set-PnPPage -Identity "$PageName.aspx" -Publish | Out-Null

Write-Host "[OK] Strona opublikowana: $SiteUrl/SitePages/$PageName.aspx"
Write-Host "[OK] Utworzone widoki tabelaryczne:"
foreach ($item in $createdViews) {
    Write-Host ("  - {0}: {1}" -f $item.Title, $item.Url)
}
