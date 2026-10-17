"""Installateur de la surveillance deploy/install-watchdog.sh : webhook validé et jamais écrit ailleurs que dans le fichier de réglages
(droits 600), service et minuteur générés depuis les modèles du dépôt, plusieurs instances côte à côte (suffixe), refus des entrées
invalides. Vrai script bash ; `systemctl` et `curl` remplacés par de faux exécutables, dossiers jetables (rien d'installé pour de bon)."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent / "deploy"
SCRIPT = DEPLOY / "install-watchdog.sh"
BASH = shutil.which("bash")

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


if BASH is None or sys.platform == "win32":
    print("bash de type Linux requis : test de l'installateur ignoré sur cette machine")
    sys.exit(0)

WORK = Path(tempfile.mkdtemp(prefix="waseboard-install-watchdog-test-"))
BIN, LOG = WORK / "bin", WORK / "calls.log"
BIN.mkdir()
(BIN / "systemctl").write_text('#!/usr/bin/env bash\necho "systemctl $*" >> "$CALLS_LOG"\n', encoding="utf-8")
(BIN / "curl").write_text('#!/usr/bin/env bash\necho "curl $*" >> "$CALLS_LOG"\ncat >> "$CALLS_LOG"   # configuration lue sur l\'entrée standard (-K -)\n[ -e "$WORK_DIR/curl_fails" ] && exit 22\nexit 0\n', encoding="utf-8")
for f in (BIN / "systemctl", BIN / "curl"):
    f.chmod(f.stat().st_mode | stat.S_IEXEC)

WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/AbC-dEf_123"


def run(*args, webhook=WEBHOOK, sub="a"):
    systemd, conf = WORK / sub / "systemd", WORK / sub / "conf"
    env = dict(os.environ, PATH=f"{BIN}:{os.environ['PATH']}", SYSTEMD_DIR=str(systemd), CONF_DIR=str(conf), SUDO="",
               CALLS_LOG=str(LOG), WORK_DIR=str(WORK))
    env.pop("WEBHOOK_URL", None)
    if webhook is not None:
        env["WEBHOOK_URL"] = webhook
    LOG.write_text("", encoding="utf-8")
    result = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    return result, systemd, conf


try:
    print("--- Installation par défaut ---")
    r, systemd, conf = run("-U", "bob")
    env_file = conf / "watchdog.env"
    check("l'installation réussit", r.returncode == 0, r.stderr)
    text = env_file.read_text(encoding="utf-8")
    check("réglages : webhook, nom, service, sonde sur le port 5005", f"WEBHOOK_URL={WEBHOOK}" in text and "NAME=WaseBoard" in text and "UNIT=waseboard.service" in text
          and "HEALTH_URL=http://127.0.0.1:5005/health" in text, text)
    check("le fichier des réglages est en droits 600", stat.S_IMODE(env_file.stat().st_mode) == 0o600, oct(stat.S_IMODE(env_file.stat().st_mode)))
    service = (systemd / "waseboard-watchdog.service").read_text(encoding="utf-8")
    timer = (systemd / "waseboard-watchdog.timer").read_text(encoding="utf-8")
    check("le service est tiré du modèle : utilisateur, chemin du script, fichier de réglages",
          "User=bob" in service and f"ExecStart={DEPLOY}/watchdog.sh" in service and f"EnvironmentFile=-{env_file}" in service and "VOTRE_USER" not in service, service)
    check("le minuteur est copié", "OnUnitActiveSec=1min" in timer)
    check("le webhook n'est écrit ni dans le service ni dans le minuteur", WEBHOOK not in service and WEBHOOK not in timer)
    calls = LOG.read_text(encoding="utf-8")
    check("systemd : rechargement puis activation du minuteur", "systemctl daemon-reload" in calls and "systemctl enable --now waseboard-watchdog.timer" in calls, calls)
    check("un message d'essai est posté sur le webhook", "curl" in calls and WEBHOOK in calls and "installée" in calls, calls)
    check("…dont l'adresse ne figure pas dans les arguments de curl (visibles dans la liste des processus)",
          WEBHOOK not in "\n".join(line for line in calls.splitlines() if line.startswith("curl ")), calls)
    check("le webhook n'est pas répété dans la sortie du script", WEBHOOK not in r.stdout and WEBHOOK not in r.stderr)

    print("\n--- Instance de test (suffixe, service, port) ---")
    r, systemd, conf = run("-n", "WaseBoard-test", "-u", "waseboard-test.service", "-p", "5007", "-i", "test", "-U", "bob", sub="b")
    check("l'installation d'une 2e instance réussit", r.returncode == 0, r.stderr)
    text = (conf / "watchdog-test.env").read_text(encoding="utf-8")
    check("réglages propres à l'instance", "NAME=WaseBoard-test" in text and "UNIT=waseboard-test.service" in text and "HEALTH_URL=http://127.0.0.1:5007/health" in text, text)
    service = (systemd / "waseboard-watchdog-test.service").read_text(encoding="utf-8")
    check("service et dossier d'état distincts de l'instance principale",
          f"EnvironmentFile=-{conf / 'watchdog-test.env'}" in service and "StateDirectory=waseboard-watchdog-test" in service, service)
    check("minuteur distinct activé", "systemctl enable --now waseboard-watchdog-test.timer" in LOG.read_text(encoding="utf-8"))

    print("\n--- Message d'essai impossible ---")
    (WORK / "curl_fails").write_text("", encoding="utf-8")
    r, systemd, conf = run("-U", "bob", sub="c")
    check("webhook injoignable : avertissement, mais la surveillance est installée", r.returncode == 0 and "n'a pas pu être remis" in r.stderr
          and (systemd / "waseboard-watchdog.timer").exists(), r.stderr)
    (WORK / "curl_fails").unlink()

    print("\n--- Refus ---")
    r, systemd, conf = run(webhook="https://exemple.test/hook", sub="d")
    check("ce n'est pas un webhook Discord : refusé, rien d'écrit", r.returncode != 0 and "webhook Discord" in r.stderr and not (conf / "watchdog.env").exists(), r.stderr)
    r, _, _ = run(webhook=WEBHOOK + "\nrm -rf /", sub="e")
    check("webhook avec un saut de ligne : refusé", r.returncode != 0)
    r, _, _ = run(webhook=None, sub="f")
    check("sans terminal ni WEBHOOK_URL : refusé avec une explication", r.returncode != 0 and "terminal" in r.stderr, r.stderr)
    r, _, _ = run("-p", "abc", sub="g")
    check("port invalide : refusé", r.returncode != 0 and "port" in r.stderr)
    r, _, _ = run("-i", "../x", sub="h")
    check("suffixe invalide : refusé", r.returncode != 0)
    r, _, _ = run("-n", 'a"b', sub="i")
    check("nom avec guillemet : refusé", r.returncode != 0)
    r, _, _ = run("-u", "pas un service", sub="j")
    check("nom de service invalide : refusé", r.returncode != 0)
    r, _, _ = run("-z", sub="k")
    check("option inconnue : usage, code 2", r.returncode == 2)
finally:
    shutil.rmtree(WORK, ignore_errors=True)

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
