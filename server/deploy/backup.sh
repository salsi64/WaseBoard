#!/usr/bin/env bash
# Sauvegarde des données du serveur WaseBoard : catalogue, sons (corbeille comprise), partages, réglages des
# serveurs, journal d'actions et statistiques, dans une archive .tgz datée. Les anciennes archives sont supprimées
# après une sauvegarde réussie. Sans danger pendant que le serveur tourne (les fichiers JSON sont réécrits de façon
# atomique), mais une archive prise en pleine copie d'un son peut contenir ce fichier incomplet : il est alors absent
# du catalogue et sans effet.
#
# Usage : backup.sh [-d DOSSIER_DONNEES] [-o DOSSIER_SAUVEGARDES] [-k JOURS] [-s]
#   -d  dossier des données (défaut : $WASEBOARD_DATA_DIR, sinon le dossier du serveur, parent de deploy/)
#   -o  dossier où écrire les archives (défaut : ~/waseboard-backups)
#   -k  jours de conservation (défaut : 7 ; 0 = ne rien supprimer)
#   -s  inclut aussi config.json et oauth_sessions.json — SECRETS (jeton du bot, sessions Discord) : à réserver à une
#       sauvegarde stockée en lieu sûr
#
# Restauration (serveur ARRÊTÉ) : voir le README, « Sauvegarde et restauration ».
set -euo pipefail

data_dir="${WASEBOARD_DATA_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
out_dir="$HOME/waseboard-backups"
keep_days=7
with_secrets=0

while getopts "d:o:k:sh" opt; do
    case "$opt" in
        d) data_dir="$OPTARG" ;;
        o) out_dir="$OPTARG" ;;
        k) keep_days="$OPTARG" ;;
        s) with_secrets=1 ;;
        *) echo "Usage : backup.sh [-d DOSSIER_DONNEES] [-o DOSSIER_SAUVEGARDES] [-k JOURS] [-s]  (voir l'en-tête du script)" >&2; exit 2 ;;
    esac
done

die() { echo "Sauvegarde WaseBoard : $*" >&2; exit 1; }
[[ "$keep_days" =~ ^[0-9]+$ ]] || die "-k attend un nombre de jours (reçu : $keep_days)."
[ -d "$data_dir" ] || die "dossier de données introuvable : $data_dir"
[ -f "$data_dir/sounds_data/catalog.json" ] || [ -d "$data_dir/sounds_data" ] || die "$data_dir ne contient pas de sounds_data/ : ce n'est pas un dossier de données WaseBoard."

umask 077
mkdir -p "$out_dir"

# Une seule sauvegarde à la fois (timer + lancement manuel simultanés).
exec 9>"$out_dir/.lock"
flock -n 9 || { echo "Sauvegarde WaseBoard : une autre sauvegarde est déjà en cours, rien à faire."; exit 0; }

items=()
for item in sounds_data shared_categories.json guild_settings.json audit.jsonl stats.jsonl; do
    [ -e "$data_dir/$item" ] && items+=("$item")
done
if [ "$with_secrets" = 1 ]; then
    for item in config.json oauth_sessions.json; do
        [ -e "$data_dir/$item" ] && items+=("$item")
    done
fi
[ "${#items[@]}" -gt 0 ] || die "rien à sauvegarder dans $data_dir."

stamp="$(date +%Y%m%d-%H%M%S)"
final="$out_dir/waseboard-data-$stamp.tgz"
tmp="$final.part"
trap 'rm -f "$tmp"' EXIT

# tar renvoie 1 quand un fichier a changé pendant la lecture : acceptable ici (voir plus haut). Plus que 1 = vraie erreur.
status=0
tar czf "$tmp" -C "$data_dir" "${items[@]}" || status=$?
[ "$status" -le 1 ] || die "tar a échoué (code $status)."

# Vérifie que l'archive se relit en entier, et qu'elle contient le catalogue quand il existe.
listing="$(tar tzf "$tmp")" || die "l'archive créée est illisible : elle est écartée, les anciennes sont conservées."
if [ -f "$data_dir/sounds_data/catalog.json" ]; then
    # (la liste est d'abord mise en variable : « tar | grep -q » + pipefail ferait échouer le test sur un SIGPIPE)
    grep -qx 'sounds_data/catalog.json' <<<"$listing" || die "le catalogue manque dans l'archive : elle est écartée."
fi

mv "$tmp" "$final"
echo "Sauvegarde WaseBoard : $final ($(du -h "$final" | cut -f1), ${#items[@]} éléments)"

if [ "$keep_days" -gt 0 ]; then
    removed="$(find "$out_dir" -maxdepth 1 -name 'waseboard-data-*.tgz' -mtime "+$keep_days" -print -delete | wc -l)"
    [ "$removed" -eq 0 ] || echo "Sauvegarde WaseBoard : $removed archive(s) de plus de $keep_days jours supprimée(s)."
fi
