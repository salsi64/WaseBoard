#!/usr/bin/env bash
# Surveillance INDÉPENDANTE du serveur WaseBoard : prévient sur un webhook Discord (salon privé) quand le service est
# arrêté ou que /health ne répond plus, puis quand tout est rétabli. Lancée chaque minute par waseboard-watchdog.timer.
#
# Pourquoi un script à part : le bot sait prévenir son propriétaire en message privé (alertes du serveur), mais un
# serveur complètement tombé ne peut plus rien envoyer. Ce script tourne en dehors de lui.
#
# Configuration : variables d'environnement, normalement lues dans /etc/waseboard/watchdog.env (voir waseboard-watchdog.service) :
#   WEBHOOK_URL         adresse du webhook Discord (Paramètres du salon > Intégrations > Webhooks). SECRET : à ne jamais
#                       mettre dans le dépôt ; le fichier doit appartenir à root, droits 600.
#   NAME                nom affiché dans les messages (défaut : WaseBoard) — utile pour distinguer test et production
#   UNIT                service à surveiller (défaut : waseboard.service)
#   HEALTH_URL          sonde HTTP (défaut : http://127.0.0.1:5005/health ; répond 503 si le bot est déconnecté de Discord)
#   FAILS_BEFORE_ALERT  contrôles ratés d'affilée avant d'alerter (défaut : 3, soit ~3 min : un redémarrage ne déclenche rien)
#   REMINDER_EVERY      secondes entre deux rappels tant que ça dure (défaut : 3600)
#   STATE_DIR           mémoire entre deux passages (défaut : $STATE_DIRECTORY de systemd, sinon /var/lib/waseboard-watchdog)
# Sans WEBHOOK_URL, le script ne fait que journaliser (stderr).
#
# Il n'envoie qu'aux changements d'état : « tombé » (après FAILS_BEFORE_ALERT échecs), rappels, puis « rétabli ». Un message
# qui n'a pas pu être remis (webhook invalide, réseau coupé) est retenté au passage suivant.
set -u

CONF="${WASEBOARD_WATCHDOG_CONF:-/etc/waseboard/watchdog.env}"
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"

NAME="${NAME:-WaseBoard}"
UNIT="${UNIT:-waseboard.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:5005/health}"
WEBHOOK_URL="${WEBHOOK_URL:-}"
STATE_DIR="${STATE_DIR:-${STATE_DIRECTORY:-/var/lib/waseboard-watchdog}}"
FAILS_BEFORE_ALERT="${FAILS_BEFORE_ALERT:-3}"
REMINDER_EVERY="${REMINDER_EVERY:-3600}"
NOW="${WATCHDOG_NOW:-$(date +%s)}"   # WATCHDOG_NOW : pour les tests uniquement

mkdir -p "$STATE_DIR" || { echo "watchdog : dossier d'état inutilisable : $STATE_DIR" >&2; exit 1; }
fails_file="$STATE_DIR/fails"          # nombre de contrôles ratés d'affilée
since_file="$STATE_DIR/down_since"     # début de la panne (epoch)
alert_file="$STATE_DIR/last_alert"     # dernier message « tombé » ou rappel remis (epoch) ; absent = rien d'annoncé

read_number() { local v; v="$(cat "$1" 2>/dev/null)"; [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo 0; }

json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e ':a;N;$!ba;s/\n/\\n/g'
}

# post MESSAGE : 0 si remis (ou si aucun webhook n'est configuré : on ne réessaie pas indéfiniment)
post() {
    echo "watchdog : $1" >&2
    [ -n "$WEBHOOK_URL" ] || return 0
    local payload
    payload="{\"content\":\"$(json_escape "$1")\",\"allowed_mentions\":{\"parse\":[]}}"
    curl -fsS -m 10 -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK_URL" >/dev/null 2>&1
}

minutes() { echo $(( ($1 + 30) / 60 )); }

# ---------- Le contrôle ----------
reason=""
if ! systemctl is-active --quiet "$UNIT"; then
    reason="le service $UNIT n'est pas actif ($(systemctl is-active "$UNIT" 2>/dev/null || true))"
elif ! curl -fsS -m 8 -o /dev/null "$HEALTH_URL" 2>/dev/null; then
    reason="le service tourne mais $HEALTH_URL ne répond pas (bot déconnecté de Discord ou serveur bloqué)"
fi

if [ -z "$reason" ]; then
    # Tout va bien. Si une panne avait été annoncée, on annonce le retour ; sinon (panne trop brève), on ne dit rien.
    if [ -e "$alert_file" ]; then
        since="$(read_number "$since_file")"
        if post "🟢 **$NAME** fonctionne de nouveau (indisponible environ $(minutes $(( NOW - since ))) min)."; then
            rm -f "$fails_file" "$since_file" "$alert_file"
        fi
    else
        rm -f "$fails_file" "$since_file"
    fi
    exit 0
fi

# ---------- Panne ----------
fails=$(( $(read_number "$fails_file") + 1 ))
echo "$fails" > "$fails_file"
[ -e "$since_file" ] || echo "$NOW" > "$since_file"
[ "$fails" -ge "$FAILS_BEFORE_ALERT" ] || { echo "watchdog : contrôle raté ($fails/$FAILS_BEFORE_ALERT) : $reason" >&2; exit 0; }

since="$(read_number "$since_file")"
if [ ! -e "$alert_file" ]; then
    post "🔴 **$NAME est tombé** : $reason." && echo "$NOW" > "$alert_file"
elif [ $(( NOW - $(read_number "$alert_file") )) -ge "$REMINDER_EVERY" ]; then
    post "🔴 **$NAME** est toujours indisponible depuis environ $(minutes $(( NOW - since ))) min : $reason." && echo "$NOW" > "$alert_file"
fi
exit 0
