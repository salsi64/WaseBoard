# Récupère la DLL compilée par GitHub Actions (micfx-build.yml) dans dist/, pour l'embarquer dans
# le client sans installer Visual Studio. Nécessite le CLI GitHub (gh) connecté au dépôt.
#
#   .\native\WaseBoardMicFx\fetch-dll.ps1                  # dernière compilation réussie de la branche courante
#   .\native\WaseBoardMicFx\fetch-dll.ps1 -Branch master

param([string]$Branch = (git rev-parse --abbrev-ref HEAD))

$ErrorActionPreference = 'Stop'
$dist = Join-Path $PSScriptRoot 'dist'

$runId = gh run list --workflow micfx-build.yml --branch $Branch --status success --limit 1 --json databaseId --jq '.[0].databaseId'
if (-not $runId) { throw "Aucune compilation réussie de l'effet micro sur la branche « $Branch »." }

$tmp = Join-Path ([IO.Path]::GetTempPath()) "waseboard-micfx-$runId"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
gh run download $runId --name WaseBoardMicFx-x64 --dir $tmp

New-Item -ItemType Directory -Force $dist | Out-Null
Copy-Item (Join-Path $tmp 'WaseBoardMicFx.dll') $dist -Force
Remove-Item $tmp -Recurse -Force
Write-Host "WaseBoardMicFx.dll (compilation $runId, branche $Branch) -> $dist"
