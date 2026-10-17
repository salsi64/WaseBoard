"""Script de sauvegarde deploy/backup.sh : archive des données, archive séparée des secrets (-c), secrets dans l'archive des données
(-s), droits 600, conservation (anciennes archives supprimées, les deux sortes), refus d'un dossier qui n'est pas un dossier de données.
Vrai script bash sur de fausses données dans un dossier jetable ; ne touche à aucune vraie sauvegarde. Ne dépend pas de server.py."""
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "deploy" / "backup.sh"
BASH = shutil.which("bash")

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


if BASH is None or sys.platform == "win32":
    print("bash/tar de type Linux requis : test de la sauvegarde ignoré sur cette machine")
    sys.exit(0)

WORK = Path(tempfile.mkdtemp(prefix="waseboard-backup-test-"))
DATA = WORK / "data"
OUT = WORK / "out"
(DATA / "sounds_data").mkdir(parents=True)
(DATA / "sounds_data" / "catalog.json").write_text('{"sounds": []}', encoding="utf-8")
(DATA / "sounds_data" / "a.mp3").write_bytes(b"x" * 1000)
(DATA / "guild_settings.json").write_text("{}", encoding="utf-8")
(DATA / "user_preferences.json").write_text('{"20": {"rev": 1, "favorites": [], "colors": {}, "categories": {}, "order": []}}', encoding="utf-8")
(DATA / "config.json").write_text('{"bot_token": "SECRET-TOKEN"}', encoding="utf-8")
(DATA / "oauth_sessions.json").write_text('{"s": 1}', encoding="utf-8")


def run(*args, data=DATA, out=OUT):
    env = dict(os.environ, HOME=str(WORK))
    return subprocess.run([BASH, str(SCRIPT), "-d", str(data), "-o", str(out), *args], capture_output=True, text=True, env=env)


def members(path):
    with tarfile.open(path) as tar:
        return sorted(tar.getnames())


def archives(kind):
    return sorted(OUT.glob(f"waseboard-{kind}-*.tgz"))


try:
    print("--- Archive des données ---")
    r = run()
    data_archives = archives("data")
    check("une sauvegarde simple réussit et écrit une archive", r.returncode == 0 and len(data_archives) == 1, r.stderr)
    names = members(data_archives[0])
    check("elle contient le catalogue, les sons et les réglages des serveurs", "sounds_data/catalog.json" in names and "sounds_data/a.mp3" in names and "guild_settings.json" in names, names)
    check("…et les préférences personnelles des membres", "user_preferences.json" in names, names)
    check("…mais ni config.json ni les sessions (secrets)", "config.json" not in names and "oauth_sessions.json" not in names)
    check("droits 600 (lisible par le seul propriétaire)", stat.S_IMODE(data_archives[0].stat().st_mode) == 0o600, oct(stat.S_IMODE(data_archives[0].stat().st_mode)))
    check("aucune archive des réglages sans -c", not archives("config"))
    check("aucun fichier temporaire laissé", not list(OUT.glob("*.part")))

    print("\n--- Archive séparée des secrets (-c) ---")
    time.sleep(1.1)  # horodatage à la seconde : deux archives distinctes
    r = run("-c")
    config_archives = archives("config")
    check("-c écrit une archive des réglages en plus de celle des données", r.returncode == 0 and len(config_archives) == 1 and len(archives("data")) == 2, r.stderr)
    check("elle contient config.json et oauth_sessions.json, rien d'autre", members(config_archives[0]) == ["config.json", "oauth_sessions.json"], members(config_archives[0]))
    check("droits 600", stat.S_IMODE(config_archives[0].stat().st_mode) == 0o600)
    newest = archives("data")[-1]
    check("l'archive des données reste sans secret", "config.json" not in members(newest))
    check("le message prévient que c'est un secret", "SECRETS" in r.stdout, r.stdout)

    print("\n--- Secrets dans l'archive des données (-s) ---")
    time.sleep(1.1)
    r = run("-s")
    check("-s inclut config.json dans l'archive des données", r.returncode == 0 and "config.json" in members(archives("data")[-1]), r.stderr)

    print("\n--- Données sans config.json ---")
    bare = WORK / "bare"
    shutil.copytree(DATA, bare)
    (bare / "config.json").unlink()
    (bare / "oauth_sessions.json").unlink()
    r = run("-c", data=bare, out=WORK / "out-bare")
    check("-c sans config.json : avertissement, pas d'archive vide, sauvegarde des données réussie",
          r.returncode == 0 and "archive des réglages ignorée" in r.stderr and len(list((WORK / "out-bare").glob("waseboard-config-*.tgz"))) == 0
          and len(list((WORK / "out-bare").glob("waseboard-data-*.tgz"))) == 1, r.stderr)

    print("\n--- Conservation ---")
    old = time.time() - 10 * 86400
    for path in list(OUT.glob("waseboard-data-*.tgz"))[:2] + list(OUT.glob("waseboard-config-*.tgz")):
        os.utime(path, (old, old))
    before = len(list(OUT.glob("waseboard-*.tgz")))
    time.sleep(1.1)
    r = run("-c", "-k", "7")
    after_names = [p.name for p in OUT.glob("waseboard-*.tgz")]
    check("les archives de plus de 7 jours (données ET réglages) sont supprimées, les récentes gardées",
          r.returncode == 0 and len(archives("config")) == 1 and len(archives("data")) == 2 and "supprimée" in r.stdout, (before, after_names, r.stdout))
    for path in OUT.glob("waseboard-*.tgz"):
        os.utime(path, (old, old))
    time.sleep(1.1)
    r = run("-k", "0")
    check("-k 0 : rien n'est jamais supprimé", r.returncode == 0 and len(list(OUT.glob("waseboard-*.tgz"))) >= 4, [p.name for p in OUT.glob("waseboard-*.tgz")])

    print("\n--- Refus ---")
    r = run(data=WORK / "inexistant", out=WORK / "out-x")
    check("dossier de données introuvable : échec explicite", r.returncode != 0 and "introuvable" in r.stderr, r.stderr)
    empty = WORK / "vide"
    empty.mkdir()
    r = run(data=empty, out=WORK / "out-y")
    check("dossier sans sounds_data/ : refusé (ce n'est pas un dossier WaseBoard)", r.returncode != 0 and "n'est pas un dossier de données" in r.stderr, r.stderr)
    r = run("-k", "abc")
    check("-k non numérique : refusé", r.returncode != 0 and "-k attend un nombre" in r.stderr, r.stderr)
    r = subprocess.run([BASH, str(SCRIPT), "-z"], capture_output=True, text=True, env=dict(os.environ, HOME=str(WORK)))
    check("option inconnue : usage, code 2", r.returncode == 2 and "Usage" in r.stderr)
finally:
    shutil.rmtree(WORK, ignore_errors=True)

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
