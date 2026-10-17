#!/usr/bin/env bash
# Installe la surveillance indépendante de WaseBoard (deploy/watchdog.sh) : fichier de réglages /etc/waseboard/…, service et minuteur systemd.
# À lancer sur la machine du serveur, par la personne qui administre :   ./deploy/install-watchdog.sh
#
# Le webhook Discord est un SECRET (qui le connaît peut écrire dans votre salon) : le script le demande au clavier, sans l'afficher, ne le
# prend jamais en argument de commande (il apparaîtrait dans l'historique et la liste des processus) et l'écrit dans un fichier root, droits 600.
# Créez-le avant : salon PRIVÉ › Modifier le salon › Intégrations › Webhooks › Nouveau webhook › Copier l'URL du webhook.
#
# Usage : install-watchdog.sh [-n NOM] [-u UNITE] [-p PORT] [-i SUFFIXE] [-U UTILISATEUR]
#   -n  nom affiché dans les messages (défaut : WaseBoard ; ex. « WaseBoard-test » pour distinguer deux instances)
#   -u  service à surveiller (défaut : waseboard.service ; instance de test : waseboard-test.service)
#   -p  port local du serveur, pour la sonde /health (défaut : 5005 ; instance de test : 5007)
#   -i  suffixe, pour surveiller plusieurs instances sur la même machine (ex. « test » : waseboard-watchdog-test.timer)
#   -U  utilisateur qui exécute le contrôle (défaut : vous)
# Variables : WEBHOOK_URL (au lieu de la saisie au clavier, pour l'automatisation) ; SYSTEMD_DIR, CONF_DIR et SUDO (tests uniquement).
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name="WaseBoard"; unit="waseboard.service"; port=5005; suffix=""; run_user="$(id -un)"
while getopts "n:u:p:i:U:h" opt; do
    case "$opt" in
        n) name="$OPTARG" ;;
        u) unit="$OPTARG" ;;
        p) port="$OPTARG" ;;
        i) suffix="-$OPTARG" ;;
        U) run_user="$OPTARG" ;;
        *) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
    esac
done
die() { echo "Installation de la surveillance : $*" >&2; exit 1; }
[[ "$port" =~ ^[0-9]+$ ]] || die "-p attend un numéro de port (reçu : $port)."
[[ "$suffix" =~ ^(-[A-Za-z0-9_]+)?$ ]] || die "-i n'accepte que des lettres, chiffres et _."
[[ "$name" != *$'\n'* && "$name" != *"'"* && "$name" != *'"'* ]] || die "-n : pas de guillemets ni de saut de ligne dans le nom."
[[ "$unit" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || die "-u attend un nom de service (ex. waseboard.service)."

systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
conf_dir="${CONF_DIR:-/etc/waseboard}"
sudo_cmd="${SUDO-sudo}"
service_name="waseboard-watchdog$suffix"
conf_file="$conf_dir/watchdog$suffix.env"
[ -f "$deploy_dir/watchdog.sh" ] || die "watchdog.sh introuvable à côté de ce script ($deploy_dir)."
[ -f "$deploy_dir/waseboard-watchdog.service" ] && [ -f "$deploy_dir/waseboard-watchdog.timer" ] || die "les modèles waseboard-watchdog.service/.timer manquent dans $deploy_dir."

webhook="${WEBHOOK_URL:-}"
if [ -z "$webhook" ]; then
    [ -t 0 ] || die "aucun terminal pour saisir le webhook : lancez le script dans un terminal, ou définissez WEBHOOK_URL."
    read -r -s -p "Adresse du webhook Discord (rien ne s'affiche en la collant) : " webhook
    echo
fi
[[ "$webhook" =~ ^https://(discord|discordapp)\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+$ ]] \
    || die "ce n'est pas l'adresse d'un webhook Discord (https://discord.com/api/webhooks/…)."

# Écriture : le fichier de réglages d'abord (root, 600), puis le service et le minuteur tirés des modèles du dépôt.
tmp_conf="$(mktemp)"; trap 'rm -f "$tmp_conf"' EXIT
umask 077
{
    echo "# Généré par install-watchdog.sh — SECRET (webhook). Ne le copiez nulle part."
    echo "NAME=$name"
    echo "UNIT=$unit"
    echo "HEALTH_URL=http://127.0.0.1:$port/health"
    echo "WEBHOOK_URL=$webhook"
} > "$tmp_conf"
$sudo_cmd mkdir -p "$conf_dir"
$sudo_cmd install -m 600 -o root -g root "$tmp_conf" "$conf_file" 2>/dev/null || $sudo_cmd install -m 600 "$tmp_conf" "$conf_file"

render() {
    sed -e "s#/home/VOTRE_USER/waseboard-server/deploy#$deploy_dir#g" \
        -e "s#VOTRE_USER#$run_user#g" \
        -e "s#/etc/waseboard/watchdog.env#$conf_file#g" \
        -e "s#StateDirectory=waseboard-watchdog#StateDirectory=$service_name#g" \
        -e "s#^Description=Surveillance indépendante de WaseBoard#Description=Surveillance indépendante de $name#" \
        -e "s#waseboard-watchdog\.service#$service_name.service#g" \
        -e "s#waseboard-watchdog\.timer#$service_name.timer#g" "$1"
}
unit_tmp="$(mktemp)"; timer_tmp="$(mktemp)"; trap 'rm -f "$tmp_conf" "$unit_tmp" "$timer_tmp"' EXIT
render "$deploy_dir/waseboard-watchdog.service" > "$unit_tmp"
render "$deploy_dir/waseboard-watchdog.timer" > "$timer_tmp"
$sudo_cmd mkdir -p "$systemd_dir"
$sudo_cmd install -m 644 "$unit_tmp" "$systemd_dir/$service_name.service"
$sudo_cmd install -m 644 "$timer_tmp" "$systemd_dir/$service_name.timer"
$sudo_cmd systemctl daemon-reload
$sudo_cmd systemctl enable --now "$service_name.timer"

# Message d'essai : confirme que le webhook fonctionne (et montre à quoi ressembleront les alertes).
# (l'adresse passe par l'entrée standard de curl, pas par ses arguments : elle n'apparaît pas dans la liste des processus)
if printf 'url = "%s"\n' "$webhook" | curl -fsS -m 10 -H 'Content-Type: application/json' \
        -d "{\"content\":\"✅ Surveillance de $name installée : je préviens ici si le service ou /health tombe.\",\"allowed_mentions\":{\"parse\":[]}}" \
        -K - >/dev/null 2>&1; then
    echo "Message d'essai envoyé : vérifiez-le dans le salon du webhook."
else
    echo "Attention : le message d'essai n'a pas pu être remis (webhook invalide ou réseau coupé) ; la surveillance est installée, vérifiez l'adresse." >&2
fi
echo "Surveillance installée : $service_name.timer (un contrôle par minute ; 'systemctl list-timers' / 'journalctl -u $service_name')."
