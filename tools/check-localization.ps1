# Vérifie les fichiers de traduction du client (src/WaseBoard/Localization/*.json) : mêmes clés dans toutes les
# langues, mêmes paramètres {0}, {1}… dans chaque texte, aucun texte vide. Lancé par la CI (client-build.yml) ;
# utilisable à la main : .\tools\check-localization.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$dir = Join-Path $root 'src/WaseBoard/Localization'

$languages = @{}
foreach ($file in Get-ChildItem $dir -Filter *.json) {
    $json = Get-Content $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $map = @{}
    foreach ($property in $json.PSObject.Properties) { $map[$property.Name] = [string]$property.Value }
    $languages[$file.BaseName] = $map
}
if (-not $languages.ContainsKey('fr')) { throw "Localization/fr.json est la langue de référence : il est introuvable." }

$errors = @()
$reference = $languages['fr']
foreach ($name in $languages.Keys | Sort-Object) {
    $map = $languages[$name]
    Write-Host ("{0} : {1} textes" -f $name, $map.Count)
    foreach ($key in $reference.Keys) {
        if (-not $map.ContainsKey($key)) { $errors += "[$name] clé manquante : $key" }
    }
    foreach ($key in $map.Keys) {
        if (-not $reference.ContainsKey($key)) { $errors += "[$name] clé inconnue du français : $key" }
        if ([string]::IsNullOrWhiteSpace($map[$key])) { $errors += "[$name] texte vide : $key" }
    }
    foreach ($key in $map.Keys) {
        if (-not $reference.ContainsKey($key)) { continue }
        $expected = ([regex]::Matches($reference[$key], '\{(\d+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique) -join ','
        $actual = ([regex]::Matches($map[$key], '\{(\d+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique) -join ','
        if ($expected -ne $actual) { $errors += "[$name] paramètres différents pour $key : attendu {$expected}, trouvé {$actual}" }
    }
}

# Aucun texte français en dur dans le code : une chaîne ou un attribut XAML avec une lettre accentuée française
# (ou un texte « humain » en français) doit passer par Loc / {loc:Loc}. Exceptions : commentaires, et les lignes
# marquées « loc-ok » (nom de langue affiché dans sa propre langue, par exemple).
$sourceRoot = Join-Path $root 'src/WaseBoard'
$accent = '[éèêëàâäùûüôöîïçœÉÈÀÇ«»]'
$sources = Get-ChildItem $sourceRoot -Recurse -Include *.cs, *.xaml |
    Where-Object { $_.FullName -notmatch '[\\/](obj|bin|Localization)[\\/]' }
foreach ($file in $sources) {
    $lineNumber = 0
    foreach ($line in Get-Content $file.FullName -Encoding UTF8) {
        $lineNumber++
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith('//') -or $trimmed.StartsWith('*') -or $trimmed.StartsWith('<!--') -or $line -match 'loc-ok') { continue }
        if ($file.Extension -eq '.cs') { $code = $line -replace '(^|\s)//.*$', '' } else { $code = $line }
        $literals = [regex]::Matches($code, '"([^"]*)"') | ForEach-Object { $_.Groups[1].Value }
        foreach ($literal in $literals) {
            if ($literal -match $accent -and $literal -notmatch '^\{loc:Loc ') {
                $relative = $file.FullName.Substring($root.Length + 1)
                $errors += "texte français en dur dans ${relative}:${lineNumber} : $literal"
            }
        }
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ -ErrorAction Continue }
    exit 1
}
Write-Host "Traductions cohérentes."
