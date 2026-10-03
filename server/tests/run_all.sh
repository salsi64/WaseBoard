#!/usr/bin/env bash
# Lance toute la suite de tests du serveur : chaque fichier test_*.py dans son propre processus Python, avec
# un dossier de données isolé et jetable (voir _env.py) — jamais le vrai dossier du serveur. Rien ne modifie
# ni ne lit les vraies données (catalogue, corbeille, réglages) d'une instance déployée.
#
# Prérequis : pip install -r ../requirements.txt, ffmpeg + libopus0 installés (sinon quelques vérifications de
# diagnostics.py tournent en ❌ au lieu de ✅, elles ne plantent pas). test_diagnostic.py fait deux appels réels
# à discord.com avec un jeton volontairement invalide : nécessite un accès réseau sortant.
#
# Usage : ./run_all.sh (depuis ce dossier, ou depuis n'importe où : le script se place lui-même)
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

status=0
total_files=0
for f in test_*.py; do
    total_files=$((total_files + 1))
    echo "=== $f ==="
    "$PY" "$f"
    code=$?
    [ "$code" -eq 0 ] || { echo "❌ $f a échoué (code $code)"; status=1; }
    echo
done

if [ "$status" -eq 0 ]; then
    echo "✅ $total_files fichier(s) de test, tous au vert."
else
    echo "❌ au moins un fichier de test a échoué — voir le détail ci-dessus."
fi
exit $status
