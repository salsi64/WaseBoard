<#
.SYNOPSIS
    Assistant d'installation du serveur WaseBoard (Docker) - Windows (Docker Desktop).

.DESCRIPTION
    Pose quelques questions, ecrit le fichier .env, verifie la configuration puis demarre le serveur.
    Relancable sans risque : une configuration existante n'est jamais ecrasee sans copie de sauvegarde.
    Sans questions : passez les valeurs en parametres (ou dans les variables d'environnement WASEBOARD_*).

.EXAMPLE
    .\setup.ps1
    Si l'execution de scripts est bloquee : powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>
param(
    [string]$BotToken = $env:WASEBOARD_BOT_TOKEN,
    [string]$OAuthSecret = $env:WASEBOARD_OAUTH2_CLIENT_SECRET,
    [string]$GuildId = $env:WASEBOARD_GUILD_ID,
    [ValidateSet('', 'https', 'cloudflare', 'local', 'proxy')][string]$Mode = $env:WASEBOARD_MODE,
    [string]$Domain = $env:WASEBOARD_DOMAIN,
    [string]$PublicUrl = $env:WASEBOARD_PUBLIC_URL,
    [string]$DuckDnsSubdomain = $env:DUCKDNS_SUBDOMAIN,
    [string]$DuckDnsToken = $env:DUCKDNS_TOKEN,
    [string]$SharedSecret = $env:WASEBOARD_SHARED_SECRET,
    [string]$Port = $env:WASEBOARD_PORT,
    [string]$CloudflareToken = $env:CLOUDFLARE_API_TOKEN,
    [string]$HttpsPort = $env:WASEBOARD_HTTPS_PORT,
    [switch]$CloudflareDdns,
    [switch]$AssumeYes
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8   # le conteneur affiche de l'UTF-8 (accents, emojis)
Set-Location -LiteralPath $PSScriptRoot
if ($env:WASEBOARD_ASSUME_YES -eq '1') { $AssumeYes = $true }
$guildGiven = $PSBoundParameters.ContainsKey('GuildId') -or ($null -ne $env:WASEBOARD_GUILD_ID)
$GuildId = "$GuildId".Trim()

function Say([string]$text = '') { Write-Host $text }
function Fail([string]$text) { Write-Host ''; Write-Host "[ERREUR] $text" -ForegroundColor Red; exit 1 }
function Ask([string]$question, [string]$default = '') {
    $suffix = ''; if ($default) { $suffix = " [$default]" }
    $reply = Read-Host -Prompt "$question$suffix"
    if ([string]::IsNullOrWhiteSpace($reply)) { return $default }
    return $reply.Trim()
}
function AskSecret([string]$question) {
    $secure = Read-Host -Prompt $question -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
function YesNo([string]$question, [bool]$defaultYes = $true) {
    $hint = '[o/N]'; if ($defaultYes) { $hint = '[O/n]' }
    $reply = Read-Host -Prompt "$question $hint"
    if ([string]::IsNullOrWhiteSpace($reply)) { return $defaultYes }
    return ($reply.Trim().Substring(0, 1) -match '[OoYy]')
}

# --- 1. Docker -------------------------------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker n'est pas installe. Installez Docker Desktop (https://www.docker.com/products/docker-desktop/) puis relancez ce script."
}
docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Docker est installe mais ne repond pas : demarrez Docker Desktop, attendez qu'il soit pret, puis relancez." }
$ComposeExe = 'docker'; $ComposePre = @('compose')
docker compose version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) { $ComposeExe = 'docker-compose'; $ComposePre = @() }
    else { Fail "Docker Compose est introuvable (il est inclus dans Docker Desktop). Mettez Docker Desktop a jour." }
}
function Compose { & $ComposeExe @ComposePre @args }
$ComposeText = (@($ComposeExe) + $ComposePre) -join ' '

Say '=== Installation du serveur WaseBoard ==='
Say ''

# --- 2. Configuration ------------------------------------------------------------------------------------------
$writeEnv = $true
if (Test-Path -LiteralPath '.env') {
    if ($AssumeYes -or (YesNo 'Une configuration (.env) existe deja. La conserver et simplement la verifier/demarrer ?' $true)) {
        $writeEnv = $false
        Say '-> Configuration existante conservee.'
    } else {
        $backup = '.env.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
        Copy-Item -LiteralPath '.env' -Destination $backup
        Say "-> Ancienne configuration sauvegardee dans $backup"
    }
}

if ($writeEnv) {
    Say 'Avant de continuer, il vous faut une application Discord (voir le README, etape 1) :'
    Say '  - un jeton de bot (Portail > Bot > Reset Token) avec "Server Members Intent" active ;'
    Say '  - un secret OAuth2 (Portail > OAuth2 > Reset Secret) et la redirection http://127.0.0.1:48899/callback/'
    Say "    (l'etape de verification ci-dessous vous dira si l'un de ces points manque)."
    Say ''

    if (-not $BotToken) { $BotToken = AskSecret 'Jeton du bot Discord (la saisie reste invisible)' }
    if (-not $BotToken) { Fail 'Le jeton du bot est obligatoire.' }
    if (-not $OAuthSecret) { $OAuthSecret = AskSecret 'Secret client OAuth2 (la saisie reste invisible)' }
    if (-not $OAuthSecret) { Fail 'Le secret OAuth2 est obligatoire : sans lui, personne ne pourrait se connecter.' }
    if (-not $guildGiven) {
        $GuildId = Ask "ID de VOTRE serveur Discord (recommande : les commandes /join... apparaissent aussitot ; Entree pour passer)"
    }

    if (-not $Mode) {
        Say ''
        Say 'Comment les utilisateurs joindront-ils ce serveur ?'
        Say '  1) Sur Internet, avec un nom de domaine et HTTPS automatique (ports 80 et 443 ouverts)'
        Say '  2) Sur Internet, HTTPS automatique avec un domaine gere par Cloudflare (aucun port 80 necessaire)'
        Say '  3) Sur mon reseau local seulement (test, sans HTTPS)'
        Say "  4) J'ai deja mon propre reverse proxy HTTPS"
        switch (Ask 'Votre choix :' '1') { '2' { $Mode = 'cloudflare' } '3' { $Mode = 'local' } '4' { $Mode = 'proxy' } default { $Mode = 'https' } }
    }

    if (-not $Port) { $Port = '5005' }
    if (-not $HttpsPort) { $HttpsPort = '443' }
    $cfToken = ''
    $profiles = ''; $domainOut = ''; $bind = '127.0.0.1'; $publicUrlOut = ''; $duckSub = ''; $duckToken = ''
    switch ($Mode) {
        'https' {
            if (-not $Domain) { $Domain = Ask 'Nom de domaine (ex : waseboard.exemple.com ou monnom.duckdns.org) :' }
            if (-not $Domain) { Fail 'Un nom de domaine est necessaire pour le HTTPS automatique.' }
            $domainOut = ($Domain -replace '^https?://', '') -replace '/.*$', ''
            $publicUrlOut = "https://$domainOut"
            $profiles = 'https'
            $duckSub = $DuckDnsSubdomain; $duckToken = $DuckDnsToken
            if (-not $duckSub -and -not $AssumeYes -and $domainOut.EndsWith('.duckdns.org')) {
                if (YesNo 'Maintenir ce sous-domaine DuckDNS a jour automatiquement ?' $true) {
                    $duckSub = $domainOut.Substring(0, $domainOut.Length - '.duckdns.org'.Length)
                    $duckToken = AskSecret 'Jeton DuckDNS (la saisie reste invisible)'
                }
            }
            if ($duckSub -and $duckToken) { $profiles = 'https,duckdns' }
            Say ''
            Say "Pensez a ouvrir les ports 80 et 443 (TCP) de votre box vers cette machine : Let's Encrypt en a besoin."
        }
        'cloudflare' {
            if (-not $Domain) { $Domain = Ask 'Nom de domaine gere par Cloudflare (ex : waseboard.exemple.com) :' }
            if (-not $Domain) { Fail 'Un nom de domaine est necessaire pour le HTTPS automatique.' }
            $domainOut = ($Domain -replace '^https?://', '') -replace '/.*$', ''
            $cfToken = $CloudflareToken
            if (-not $cfToken) {
                Say 'Il faut un jeton API Cloudflare limite a votre zone : Cloudflare > Mon profil > Jetons API > Creer un jeton >'
                Say 'modele "Modifier le DNS de la zone" (droits "Zone : Lire" et "DNS : Modifier").'
                $cfToken = AskSecret 'Jeton API Cloudflare (la saisie reste invisible)'
            }
            if (-not $cfToken) { Fail 'Le jeton API Cloudflare est obligatoire pour ce mode.' }
            $publicUrlOut = "https://$domainOut"
            if ($HttpsPort -ne '443') { $publicUrlOut = "${publicUrlOut}:$HttpsPort" }
            $profiles = 'cloudflare'
            $ddns = $CloudflareDdns.IsPresent -or ($env:WASEBOARD_CLOUDFLARE_DDNS -eq '1')
            if (-not $CloudflareDdns.IsPresent -and -not $env:WASEBOARD_CLOUDFLARE_DDNS -and -not $AssumeYes) {
                $ddns = YesNo "Garder l'enregistrement DNS $domainOut a jour avec l'IP publique de cette machine (utile si elle change) ?" $true
            }
            if ($ddns) { $profiles = 'cloudflare,cloudflare-ddns' }
            Say ''
            Say "Ouvrez le port $HttpsPort (TCP) de votre box vers cette machine ; le port 80 n'est pas necessaire."
            if (-not $ddns) { Say "L'enregistrement DNS $domainOut doit pointer vers l'adresse de cette machine (creez-le chez Cloudflare)." }
        }
        'local' {
            $bind = '0.0.0.0'
            $publicUrlOut = $PublicUrl
            if (-not $publicUrlOut) {
                $detected = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                    Where-Object { $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' } |
                    Select-Object -First 1).IPAddress
                if (-not $detected) { $detected = 'IP-DE-CETTE-MACHINE' }
                $publicUrlOut = Ask 'Adresse de cette machine pour les clients :' "http://${detected}:$Port"
            }
        }
        'proxy' {
            $publicUrlOut = $PublicUrl
            if (-not $publicUrlOut) { $publicUrlOut = Ask 'Adresse publique HTTPS (ex : https://waseboard.exemple.com) :' }
            Say ''
            Say "Votre reverse proxy doit transmettre vers http://127.0.0.1:$Port (limite d'upload conseillee : 64 Mo)."
        }
    }

    if (-not $SharedSecret) {
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $SharedSecret = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
    }

    $lines = @(
        "# Ecrit par setup.ps1 le $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Contient des secrets : ne le partagez pas.",
        "WASEBOARD_BOT_TOKEN=$BotToken",
        "WASEBOARD_OAUTH2_CLIENT_SECRET=$OAuthSecret",
        "WASEBOARD_SHARED_SECRET=$SharedSecret",
        "WASEBOARD_PUBLIC_URL=$publicUrlOut",
        "WASEBOARD_GUILD_ID=$GuildId",
        "COMPOSE_PROFILES=$profiles",
        "WASEBOARD_DOMAIN=$domainOut",
        "WASEBOARD_BIND=$bind",
        "WASEBOARD_PORT=$Port",
        "DUCKDNS_SUBDOMAIN=$duckSub",
        "DUCKDNS_TOKEN=$duckToken",
        "CLOUDFLARE_API_TOKEN=$cfToken",
        "WASEBOARD_HTTPS_PORT=$HttpsPort"
    )
    # UTF-8 SANS BOM et fins de ligne LF : un BOM corromprait le nom de la premiere variable pour Docker Compose.
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.env'), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Say ''
    Say '-> Configuration ecrite dans .env (secret partage genere automatiquement).'
}

# --- 3. Construction et verification (sans connecter le bot) -----------------------------------------------------
Say ''
Say "Construction de l'image Docker (quelques minutes la premiere fois)..."
Compose build waseboard | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "La construction de l'image a echoue. Relancez `"$ComposeText build waseboard`" pour voir l'erreur." }

Say ''
Say '=== Verification de la configuration ==='
Say "(le bot n'est pas encore connecte ; l'accessibilite depuis Internet sera testee apres le demarrage)"
while ($true) {
    Say ''
    Compose run --rm --no-deps -T waseboard python server.py --check --no-public-check
    if ($LASTEXITCODE -eq 0) { break }
    Say ''
    Say "Des points sont a corriger (lignes avec une croix rouge ci-dessus). Si le bot n'est pas encore sur votre serveur Discord,"
    Say "ouvrez l'URL d'invitation affichee, puis corrigez dans le portail ce qui est signale."
    if ($AssumeYes) { Fail 'Configuration invalide (mode sans questions).' }
    $answer = Read-Host -Prompt '[R]elancer la verification, [C]ontinuer quand meme, [Q]uitter ? (R)'
    if ($answer -match '^[Cc]') { break }
    if ($answer -match '^[Qq]') { Say 'Arret. Relancez .\setup.ps1 quand ce sera corrige (votre .env est conserve).'; exit 1 }
}

# --- 4. Demarrage ----------------------------------------------------------------------------------------------
Say ''
Say 'Demarrage du serveur...'
Compose up -d
if ($LASTEXITCODE -ne 0) { Fail "Le demarrage a echoue : voir `"$ComposeText logs waseboard`"." }
$container = (Compose ps -q waseboard | Select-Object -First 1)
$health = ''
for ($i = 0; $i -lt 60; $i++) {
    $health = (docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $container 2>$null)
    if ($health -eq 'healthy') { break }
    Start-Sleep -Seconds 2
}
if ($health -ne 'healthy') {
    Say ''
    Say "[!] Le serveur n'est pas encore healthy (etat : $health). Dernieres lignes du journal :"
    Compose logs --tail 25 waseboard
    Fail "Le demarrage a echoue ou est trop lent. Voir `"$ComposeText logs -f waseboard`"."
}

Say ''
Say '=== Verification finale ==='
Compose exec -T waseboard python server.py --check

Say ''
Say '[OK] Le serveur WaseBoard tourne.'
Say ''
Say 'Dernieres etapes, dans Discord :'
Say "  1. Tapez /diagnostic : il controle les droits du bot sur vos salons vocaux."
Say "  2. Dans le salon ou vos membres doivent recuperer leur lien, tapez /configurer-invitation :"
Say "     le bouton poste leur donne un lien qui ouvre WaseBoard deja connecte a votre serveur."
Say ''
Say "Utile : `"$ComposeText logs -f waseboard`" (journal), `"$ComposeText up -d --build`" (apres une mise a jour),"
Say '        donnees dans le volume Docker "waseboard_waseboard-data" (a sauvegarder, voir le README).'
