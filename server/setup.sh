#!/usr/bin/env bash
# Assistant d'installation du serveur WaseBoard (Docker) — Linux / macOS.
# Pose quelques questions, écrit le fichier .env, vérifie la configuration puis démarre le serveur.
# Relançable sans risque : une configuration existante n'est jamais écrasée sans copie de sauvegarde.
#
# Mode sans questions : fournissez les valeurs par variables d'environnement (mêmes noms que dans .env) —
#   WASEBOARD_BOT_TOKEN, WASEBOARD_OAUTH2_CLIENT_SECRET, WASEBOARD_GUILD_ID (facultatif),
#   WASEBOARD_MODE (https | local | proxy), WASEBOARD_DOMAIN (https), WASEBOARD_PUBLIC_URL (local | proxy),
#   DUCKDNS_SUBDOMAIN + DUCKDNS_TOKEN (https, facultatif), WASEBOARD_PORT (port de la machine, 5005 par défaut),
#   WASEBOARD_ASSUME_YES=1.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

say() { printf '%s\n' "$*"; }
die() { printf '\n❌ %s\n' "$*" >&2; exit 1; }
ask() { # ask "question" [défaut] -> réponse sur stdout (la question s'affiche sur stderr)
    local reply=""; read -r -p "$1${2:+ [$2]} " reply || true; printf '%s' "${reply:-${2:-}}"
}
ask_secret() { local reply=""; read -r -s -p "$1 " reply || true; echo >&2; printf '%s' "$reply"; }
yes_no() { # yes_no "question" O|N -> code 0 si oui ; la réponse par défaut est celle en majuscule
    local def="${2:-O}" hint reply=""
    if [ "$def" = "O" ]; then hint="[O/n]"; else hint="[o/N]"; fi
    read -r -p "$1 $hint " reply || true
    case "${reply:-$def}" in [OoYy]*) return 0 ;; *) return 1 ;; esac
}
assume_yes() { [ "${WASEBOARD_ASSUME_YES:-}" = "1" ]; }

# --- 1. Docker ------------------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker n'est pas installé. Voir https://docs.docker.com/engine/install/ puis relancez ce script."
docker info >/dev/null 2>&1 || die "Docker est installé mais inaccessible (démon arrêté, ou droits insuffisants). Essayez avec sudo, ou ajoutez-vous au groupe « docker »."
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE=(docker-compose)
else die "Docker Compose est introuvable. Installez le plugin « docker-compose-plugin » (https://docs.docker.com/compose/install/)."
fi

say "=== Installation du serveur WaseBoard ==="
say ""

# --- 2. Configuration -------------------------------------------------------------------------------------------
write_env=1
if [ -f .env ]; then
    if assume_yes || yes_no "Une configuration (.env) existe déjà. La conserver et simplement la vérifier/démarrer ?" O; then
        write_env=0
        say "→ Configuration existante conservée."
    else
        backup=".env.bak-$(date +%Y%m%d-%H%M%S)"
        cp -p .env "$backup"
        say "→ Ancienne configuration sauvegardée dans $backup"
    fi
fi

if [ "$write_env" = 1 ]; then
    say "Avant de continuer, il vous faut une application Discord (voir le README, étape 1) :"
    say "  • un jeton de bot (Portail > Bot > Reset Token) avec « Server Members Intent » activé ;"
    say "  • un secret OAuth2 (Portail > OAuth2 > Reset Secret) et la redirection http://127.0.0.1:48899/callback/"
    say "    (l'étape de vérification ci-dessous vous dira si l'un de ces points manque)."
    say ""

    bot_token="${WASEBOARD_BOT_TOKEN:-}"
    [ -n "$bot_token" ] || bot_token="$(ask_secret "Jeton du bot Discord (la saisie reste invisible) :")"
    [ -n "$bot_token" ] || die "Le jeton du bot est obligatoire."
    oauth_secret="${WASEBOARD_OAUTH2_CLIENT_SECRET:-}"
    [ -n "$oauth_secret" ] || oauth_secret="$(ask_secret "Secret client OAuth2 (la saisie reste invisible) :")"
    [ -n "$oauth_secret" ] || die "Le secret OAuth2 est obligatoire : sans lui, personne ne pourrait se connecter."
    guild_id="${WASEBOARD_GUILD_ID-}"
    if [ -z "${WASEBOARD_GUILD_ID+x}" ]; then
        guild_id="$(ask "ID de VOTRE serveur Discord (recommandé : les commandes /join... apparaissent aussitôt ; Entrée pour passer) :")"
    fi

    mode="${WASEBOARD_MODE:-}"
    if [ -z "$mode" ]; then
        say ""
        say "Comment les utilisateurs joindront-ils ce serveur ?"
        say "  1) Sur Internet, avec un nom de domaine et HTTPS automatique (recommandé)"
        say "  2) Sur mon réseau local seulement (test, sans HTTPS)"
        say "  3) J'ai déjà mon propre reverse proxy HTTPS"
        case "$(ask "Votre choix :" "1")" in
            2) mode=local ;;
            3) mode=proxy ;;
            *) mode=https ;;
        esac
    fi

    port="${WASEBOARD_PORT:-5005}"
    profiles=""; domain=""; bind="127.0.0.1"; public_url=""; duck_sub=""; duck_token=""
    case "$mode" in
        https)
            domain="${WASEBOARD_DOMAIN:-}"
            [ -n "$domain" ] || domain="$(ask "Nom de domaine (ex : waseboard.exemple.com ou monnom.duckdns.org) :")"
            [ -n "$domain" ] || die "Un nom de domaine est nécessaire pour le HTTPS automatique."
            domain="${domain#https://}"; domain="${domain%%/*}"
            public_url="https://$domain"
            profiles="https"
            duck_sub="${DUCKDNS_SUBDOMAIN:-}"; duck_token="${DUCKDNS_TOKEN:-}"
            if [ -z "$duck_sub" ] && [ -z "${WASEBOARD_ASSUME_YES:-}" ] && [ "${domain%.duckdns.org}" != "$domain" ]; then
                if yes_no "Maintenir ce sous-domaine DuckDNS à jour automatiquement ?" O; then
                    duck_sub="${domain%.duckdns.org}"
                    duck_token="$(ask_secret "Jeton DuckDNS (la saisie reste invisible) :")"
                fi
            fi
            if [ -n "$duck_sub" ] && [ -n "$duck_token" ]; then profiles="https,duckdns"; fi
            say ""
            say "Pensez à ouvrir les ports 80 et 443 (TCP) de votre box vers cette machine : Let's Encrypt en a besoin."
            ;;
        local)
            bind="0.0.0.0"
            public_url="${WASEBOARD_PUBLIC_URL:-}"
            if [ -z "$public_url" ]; then
                detected="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
                public_url="$(ask "Adresse de cette machine pour les clients :" "http://${detected:-IP-DE-CETTE-MACHINE}:$port")"
            fi
            ;;
        proxy)
            bind="127.0.0.1"
            public_url="${WASEBOARD_PUBLIC_URL:-}"
            [ -n "$public_url" ] || public_url="$(ask "Adresse publique HTTPS (ex : https://waseboard.exemple.com) :")"
            say ""
            say "Votre reverse proxy doit transmettre vers http://127.0.0.1:$port (limite d'upload conseillée : 64 Mo)."
            ;;
        *) die "Mode inconnu : $mode (attendu : https, local ou proxy)." ;;
    esac

    secret="${WASEBOARD_SHARED_SECRET:-}"
    if [ -z "$secret" ]; then
        if command -v openssl >/dev/null 2>&1; then secret="$(openssl rand -hex 32)"
        else secret="$(head -c 256 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 64)"; fi
    fi

    umask 077
    {
        echo "# Écrit par setup.sh le $(date '+%Y-%m-%d %H:%M'). Contient des secrets : ne le partagez pas."
        echo "WASEBOARD_BOT_TOKEN=$bot_token"
        echo "WASEBOARD_OAUTH2_CLIENT_SECRET=$oauth_secret"
        echo "WASEBOARD_SHARED_SECRET=$secret"
        echo "WASEBOARD_PUBLIC_URL=$public_url"
        echo "WASEBOARD_GUILD_ID=$guild_id"
        echo "COMPOSE_PROFILES=$profiles"
        echo "WASEBOARD_DOMAIN=$domain"
        echo "WASEBOARD_BIND=$bind"
        echo "WASEBOARD_PORT=$port"
        echo "DUCKDNS_SUBDOMAIN=$duck_sub"
        echo "DUCKDNS_TOKEN=$duck_token"
    } > .env
    chmod 600 .env
    say ""
    say "→ Configuration écrite dans .env (secret partagé généré automatiquement)."
fi

# --- 3. Construction et vérification (sans connecter le bot) ------------------------------------------------------
say ""
say "Construction de l'image Docker (quelques minutes la première fois)..."
"${COMPOSE[@]}" build waseboard >/dev/null || die "La construction de l'image a échoué. Relancez « ${COMPOSE[*]} build waseboard » pour voir l'erreur."

say ""
say "=== Vérification de la configuration ==="
say "(le bot n'est pas encore connecté ; l'accessibilité depuis Internet sera testée après le démarrage)"
while true; do
    say ""
    set +e
    "${COMPOSE[@]}" run --rm --no-deps -T waseboard python server.py --check --no-public-check
    status=$?
    set -e
    [ "$status" = 0 ] && break
    say ""
    say "Des points sont à corriger (lignes ❌ ci-dessus). Si le bot n'est pas encore sur votre serveur Discord,"
    say "ouvrez l'URL d'invitation affichée, puis corrigez dans le portail ce qui est signalé."
    if assume_yes; then die "Configuration invalide (mode sans questions)."; fi
    read -r -p "[R]elancer la vérification, [C]ontinuer quand même, [Q]uitter ? (R) " answer || true
    case "${answer:-R}" in
        [Cc]*) break ;;
        [Qq]*) say "Arrêt. Relancez ./setup.sh quand ce sera corrigé (votre .env est conservé)."; exit 1 ;;
    esac
done

# --- 4. Démarrage -----------------------------------------------------------------------------------------------
say ""
say "Démarrage du serveur..."
"${COMPOSE[@]}" up -d
container="$("${COMPOSE[@]}" ps -q waseboard)"
health=""
for _ in $(seq 1 60); do
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
    [ "$health" = "healthy" ] && break
    sleep 2
done
if [ "$health" != "healthy" ]; then
    say ""
    say "⚠️ Le serveur n'est pas encore « healthy » (état : ${health:-inconnu}). Dernières lignes du journal :"
    "${COMPOSE[@]}" logs --tail 25 waseboard || true
    die "Le démarrage a échoué ou est trop lent. Voir « ${COMPOSE[*]} logs -f waseboard »."
fi

say ""
say "=== Vérification finale ==="
"${COMPOSE[@]}" exec -T waseboard python server.py --check || true

say ""
say "✅ Le serveur WaseBoard tourne."
say ""
say "Dernières étapes, dans Discord :"
say "  1. Tapez /diagnostic : il contrôle les droits du bot sur vos salons vocaux."
say "  2. Dans le salon où vos membres doivent récupérer leur lien, tapez /configurer-invitation :"
say "     le bouton posté leur donne un lien qui ouvre WaseBoard déjà connecté à votre serveur."
say ""
say "Utile : « ${COMPOSE[*]} logs -f waseboard » (journal), « ${COMPOSE[*]} up -d --build » (après une mise à jour),"
say "        données dans le volume Docker « waseboard_waseboard-data » (à sauvegarder, voir le README)."
