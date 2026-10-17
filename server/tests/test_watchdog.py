"""Script de surveillance indépendant deploy/watchdog.sh : alerte sur webhook quand le service ou /health tombe, rappels,
message de retour, aucun message pour une coupure brève. Vrai script bash, `systemctl` et `curl` remplacés par de faux
exécutables (aucun réseau, aucun service touché). Ne dépend pas de server.py."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "deploy" / "watchdog.sh"
BASH = shutil.which("bash")

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


if BASH is None:
    print("bash introuvable : test du watchdog ignoré")
    sys.exit(0)

WORK = Path(tempfile.mkdtemp(prefix="waseboard-watchdog-test-"))
BIN, STUB, STATE = WORK / "bin", WORK / "stub", WORK / "state"
BIN.mkdir(); STUB.mkdir()

(BIN / "systemctl").write_text('''#!/usr/bin/env bash
# faux systemctl : « is-active [--quiet] UNITE » lit l'état dans $STUB_DIR/unit_state
state="$(cat "$STUB_DIR/unit_state" 2>/dev/null || echo active)"
args=("$@"); quiet=0
for a in "${args[@]}"; do [ "$a" = "--quiet" ] && quiet=1; done
[ "$quiet" = 1 ] || echo "$state"
[ "$state" = active ]
''', encoding="utf-8")
(BIN / "curl").write_text('''#!/usr/bin/env bash
# faux curl : le webhook (-d ...) journalise le corps ; toute autre adresse est la sonde /health
url=""; body=""
while [ $# -gt 0 ]; do
    case "$1" in
        -d) body="$2"; shift 2 ;;
        -H|-m|-o) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
if [ -n "$body" ]; then
    printf '%s\\n' "$body" >> "$STUB_DIR/webhook.log"
    [ -e "$STUB_DIR/webhook_fails" ] && exit 22
    exit 0
fi
[ -e "$STUB_DIR/health_fails" ] && exit 22
exit 0
''', encoding="utf-8")
for f in (BIN / "systemctl", BIN / "curl"):
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


def set_unit(state): (STUB / "unit_state").write_text(state, encoding="utf-8")
def flag(name, on):
    p = STUB / name
    p.touch() if on else p.unlink(missing_ok=True)

def posts():
    log = STUB / "webhook.log"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

def reset():
    shutil.rmtree(STATE, ignore_errors=True)
    (STUB / "webhook.log").unlink(missing_ok=True)
    set_unit("active"); flag("health_fails", False); flag("webhook_fails", False)


def run(now=1_000_000, **extra):
    env = {"PATH": f"{BIN}{os.pathsep}{os.environ['PATH']}", "STUB_DIR": str(STUB), "STATE_DIR": str(STATE),
           "WASEBOARD_WATCHDOG_CONF": str(WORK / "pas-de-conf.env"), "WEBHOOK_URL": "https://discord.test/webhook/secret",
           "UNIT": "waseboard.service", "HEALTH_URL": "http://127.0.0.1:5005/health", "WATCHDOG_NOW": str(now), "HOME": str(WORK)}
    env.update(extra)
    env = {k: v for k, v in env.items() if v is not None}
    return subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8")


print("\n--- Syntaxe et fichiers ---")
check("bash -n : le script est syntaxiquement correct", subprocess.run([BASH, "-n", str(SCRIPT)]).returncode == 0)
deploy = SCRIPT.parent
service = (deploy / "waseboard-watchdog.service").read_text(encoding="utf-8")
timer = (deploy / "waseboard-watchdog.timer").read_text(encoding="utf-8")
check("unité systemd : une exécution unique, fichier d'environnement facultatif, mémoire d'état gérée par systemd",
      "Type=oneshot" in service and "EnvironmentFile=-/etc/waseboard/watchdog.env" in service and "StateDirectory=waseboard-watchdog" in service)
check("minuterie : un passage par minute, pas pendant le démarrage de la machine", "OnUnitActiveSec=1min" in timer and "OnBootSec=3min" in timer)
import re
check("aucun vrai webhook (secret) écrit dans les fichiers de déploiement",
      not any(re.search(r"discord(?:app)?\.com/api/webhooks/\d", f.read_text(encoding="utf-8")) for f in (SCRIPT, deploy / "waseboard-watchdog.service", deploy / "waseboard-watchdog.timer")))

print("\n--- Tout va bien ---")
reset()
r = run()
check("service actif et /health OK : aucun message, aucune trace", r.returncode == 0 and posts() == [] and not (STATE / "fails").exists(), r.stderr)

print("\n--- Panne : on n'alerte pas tout de suite ---")
reset(); set_unit("failed")
for i in (1, 2):
    r = run(now=1_000_000 + 60 * i)
check("2 contrôles ratés sur 3 : rien d'envoyé", posts() == [] and "(2/3)" in r.stderr, r.stderr)
set_unit("active")
r = run(now=1_000_180)
check("retour à la normale avant le seuil : rien d'envoyé, compteur remis à zéro", posts() == [] and not (STATE / "fails").exists())

print("\n--- Panne confirmée, rappels, rétablissement ---")
reset(); set_unit("inactive")
for i in (1, 2, 3):
    r = run(now=1_000_000 + 60 * i)
p = posts()
check("3e contrôle raté : UN message « tombé » avec la raison", len(p) == 1 and "est tombé" in p[0]["content"] and "inactive" in p[0]["content"] and "waseboard.service" in p[0]["content"], p)
check("le message n'ouvre aucune mention (@everyone…)", p[0]["allowed_mentions"] == {"parse": []})
run(now=1_000_000 + 60 * 4); run(now=1_000_000 + 60 * 5)
check("contrôles suivants : pas de répétition", len(posts()) == 1)
run(now=1_000_180 + 3599)
check("1 s avant l'heure : pas encore de rappel", len(posts()) == 1)
run(now=1_000_180 + 3600)
p = posts()
check("1 h après l'annonce : un rappel (« toujours indisponible », durée de la panne en minutes)", len(p) == 2 and "toujours indisponible" in p[1]["content"] and "62 min" in p[1]["content"], p)
run(now=1_000_180 + 3720)
check("…et pas un de plus aussitôt après", len(posts()) == 2)
set_unit("active")
run(now=1_000_060 + 7200)  # la panne a commencé à 1_000_060
p = posts()
check("service revenu : un message « fonctionne de nouveau » avec la durée", len(p) == 3 and "fonctionne de nouveau" in p[2]["content"] and "120 min" in p[2]["content"], p)
run(now=1_000_060 + 7300)
check("ensuite le silence, mémoire effacée", len(posts()) == 3 and not (STATE / "last_alert").exists() and not (STATE / "down_since").exists())

print("\n--- /health qui ne répond plus alors que le service tourne ---")
reset(); flag("health_fails", True)
for i in (1, 2, 3):
    run(now=2_000_000 + 60 * i)
p = posts()
check("raison explicite : le service tourne mais la sonde ne répond pas", len(p) == 1 and "tourne mais" in p[0]["content"] and "/health" in p[0]["content"], p)
flag("health_fails", False)
run(now=2_000_400)
check("sonde rétablie : message de retour", len(posts()) == 2 and "fonctionne de nouveau" in posts()[1]["content"])

print("\n--- Webhook en panne : on réessaie ---")
reset(); set_unit("failed"); flag("webhook_fails", True)
for i in (1, 2, 3):
    run(now=3_000_000 + 60 * i)
check("webhook refusé : tentative faite, rien noté comme annoncé", len(posts()) == 1 and not (STATE / "last_alert").exists())
run(now=3_000_000 + 60 * 4)
check("…nouvelle tentative au passage suivant", len(posts()) == 2)
flag("webhook_fails", False)
run(now=3_000_000 + 60 * 5)
check("…jusqu'à ce que ça passe, puis plus rien", len(posts()) == 3 and (STATE / "last_alert").exists())
run(now=3_000_000 + 60 * 6)
check("annonce remise : pas de doublon", len(posts()) == 3)

print("\n--- Sans webhook configuré ---")
reset(); set_unit("failed")
for i in (1, 2, 3):
    r = run(now=4_000_000 + 60 * i, WEBHOOK_URL="")
check("journalise seulement (stderr), aucun appel au webhook, code de sortie 0", posts() == [] and "est tombé" in r.stderr and r.returncode == 0, r.stderr)

print("\n--- Textes particuliers ---")
reset(); set_unit("failed")
for i in (1, 2, 3):
    run(now=5_000_000 + 60 * i, NAME='Wase "Board" \\ test')
p = posts()
check("un nom avec guillemets et antislash donne quand même un JSON valide", len(p) == 1 and 'Wase "Board" \\ test' in p[0]["content"], p)
reset(); set_unit("failed")
for i in (1, 2):
    run(now=6_000_000 + 60 * i, FAILS_BEFORE_ALERT="2")
check("FAILS_BEFORE_ALERT réglable", len(posts()) == 1)
r = run(STATE_DIR=str(WORK / "fichier-pas-dossier" / "x"))
check("dossier d'état créé à la demande", r.returncode == 0 and (WORK / "fichier-pas-dossier" / "x").is_dir())
(WORK / "occupé").write_text("je suis un fichier", encoding="utf-8")
r = run(STATE_DIR=str(WORK / "occupé" / "sous-dossier"))
check("dossier d'état inutilisable : erreur claire (code 1)", r.returncode == 1 and "inutilisable" in r.stderr, (r.returncode, r.stderr))

print("\n--- Fichier de configuration ---")
reset(); set_unit("failed")
conf = WORK / "watchdog.env"
conf.write_text('WEBHOOK_URL="https://discord.test/webhook/depuis-le-fichier"\nNAME="Test-instance"\nFAILS_BEFORE_ALERT=1\n', encoding="utf-8")
run(now=7_000_000, WASEBOARD_WATCHDOG_CONF=str(conf), WEBHOOK_URL=None, NAME=None, FAILS_BEFORE_ALERT=None)
p = posts()
check("les réglages viennent du fichier /etc/waseboard/watchdog.env (ici un fichier de test)", len(p) == 1 and "Test-instance" in p[0]["content"], p)

shutil.rmtree(WORK, ignore_errors=True)
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
