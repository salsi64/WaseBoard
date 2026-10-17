# Vérifie que le numéro de version est le même partout : <Version> du csproj, MyAppVersion de l'installeur et,
# si -Tag est donné (ex. v3.4.0), le tag Git. Utilisé par la CI (client-build.yml) ; utilisable à la main :
#
#   .\tools\check-versions.ps1
#   .\tools\check-versions.ps1 -Tag v3.4.0

param([string]$Tag = "")

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

$csproj = Get-Content (Join-Path $root 'src/WaseBoard/WaseBoard.csproj') -Raw
$iss = Get-Content (Join-Path $root 'installer/setup.iss') -Raw

$versionCsproj = [regex]::Match($csproj, '<Version>\s*([^<\s]+)\s*</Version>').Groups[1].Value
$versionInstaller = [regex]::Match($iss, '#define\s+MyAppVersion\s+"([^"]+)"').Groups[1].Value

if (-not $versionCsproj) { throw "Version introuvable dans src/WaseBoard/WaseBoard.csproj (balise <Version>)." }
if (-not $versionInstaller) { throw "Version introuvable dans installer/setup.iss (#define MyAppVersion)." }

Write-Host "csproj    : $versionCsproj"
Write-Host "installeur: $versionInstaller"

$errors = @()
if ($versionCsproj -ne $versionInstaller) {
    $errors += "Les versions diffèrent : csproj $versionCsproj, installeur $versionInstaller."
}
if ($Tag) {
    $versionTag = $Tag -replace '^v', ''
    Write-Host "tag       : $versionTag"
    if ($versionTag -ne $versionCsproj) {
        $errors += "Le tag $Tag ne correspond pas à la version du projet ($versionCsproj)."
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ -ErrorAction Continue }
    exit 1
}
Write-Host "Versions cohérentes : $versionCsproj"
