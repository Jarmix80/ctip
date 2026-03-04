param(
    [Parameter(Mandatory = $true)]
    [string]$SiteUrl,

    [string]$LibraryTitle = "Documents",
    [string]$ViewName = "Backup Dashboard"
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

Ensure-Module -Name "PnP.PowerShell"

Write-Host "[INFO] Laczenie z SharePoint: $SiteUrl"
Connect-PnPOnline -Url $SiteUrl -Interactive

$list = Get-PnPList -Identity $LibraryTitle -ErrorAction Stop
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
