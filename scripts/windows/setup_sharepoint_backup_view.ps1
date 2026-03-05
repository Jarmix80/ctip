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
        if ($list -and $list.BaseTemplate -eq 101) {
            return $list
        }
    }

    $all = Get-PnPList
    $docLib = $all | Where-Object { $_.BaseTemplate -eq 101 } | Select-Object -First 1
    if ($docLib) {
        Write-Host "[WARN] Nie znaleziono wskazanej biblioteki. Uzyto pierwszej dostepnej: $($docLib.Title)"
        return $docLib
    }
    throw "Nie znaleziono biblioteki dokumentow."
}

Ensure-Module -Name "PnP.PowerShell"

Write-Host "[INFO] Laczenie z SharePoint: $SiteUrl"
if ($ClientSecret -and $ClientSecret.Trim()) {
    Write-Host "[INFO] Tryb auth: app-only (client secret)"
    Connect-PnPOnline -Url $SiteUrl -ClientId $ClientId -ClientSecret $ClientSecret -Tenant $Tenant
}
else {
    Write-Host "[INFO] Tryb auth: device login"
    Connect-PnPOnline -Url $SiteUrl -DeviceLogin -ClientId $ClientId -Tenant $Tenant
}

$list = Resolve-DocumentLibrary -PreferredTitle $LibraryTitle
$LibraryTitle = $list.Title
Write-Host "[OK] Biblioteka: $($list.Title)"

$view = Get-PnPView -List $LibraryTitle | Where-Object { $_.Title -eq $ViewName } | Select-Object -First 1
if (-not $view) {
    Write-Host "[INFO] Tworzenie widoku: $ViewName"
    $view = Add-PnPView -List $LibraryTitle -Title $ViewName -Fields @(
        "DocIcon",
        "LinkFilename",
        "File_x0020_Type",
        "FileSizeDisplay",
        "Modified",
        "Editor"
    ) -Paged:$true -RowLimit 200
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

Set-PnPView -List $LibraryTitle -Identity $view.Id -Fields @(
    "DocIcon",
    "LinkFilename",
    "File_x0020_Type",
    "FileSizeDisplay",
    "Modified",
    "Editor"
) -Values @{
    RowLimit = 200
    Paged = "TRUE"
    Query = $query
}

Set-PnPView -List $LibraryTitle -Identity $view.Id -SetAsDefault

Write-Host "[OK] Widok '$ViewName' zostal ustawiony jako domyslny."
Write-Host "[OK] Uklad: grupowanie po folderach + sortowanie po dacie modyfikacji malejaco."
