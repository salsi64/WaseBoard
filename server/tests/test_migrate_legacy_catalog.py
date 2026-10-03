"""deploy/migrate_legacy_catalog.py (migration ponctuelle du catalogue pré-isolation par guilde, voir
CUTOVER.md) : jeu de données fabriqué, jamais les vraies données de prod. Lancé via tests/run_all.sh."""
import json
import subprocess
import sys

import _env  # noqa: E402  (dossier de données isolé et jetable)

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


SCRIPT = _env.SERVER_DIR / "deploy" / "migrate_legacy_catalog.py"
DATA = _env.DATA_DIR
SOUNDS = DATA / "sounds_data"
SOUNDS.mkdir(parents=True, exist_ok=True)
CATALOG = SOUNDS / "catalog.json"
SHARED = DATA / "shared_categories.json"

NATIVE = "1000000000000000001"
OTHER_A = "1000000000000000002"
OTHER_B = "1000000000000000003"
FOREIGN = "1000000000000000099"  # ne reçoit jamais de partage : sert à vérifier l'absence de fuite


def write_catalog(entries):
    CATALOG.write_text(json.dumps(entries), encoding="utf-8")


def read_catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def read_shared():
    return json.loads(SHARED.read_text(encoding="utf-8")) if SHARED.exists() else {}


def run(*args, expect_ok=True):
    r = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(DATA), *args], capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    return r


# ---------- Cas de base : sons sans guild_id, un son déjà migré, un son d'une guilde tierce ----------
write_catalog([
    {"id": "legacy1", "name": "a"},                              # pas de guild_id du tout : à migrer
    {"id": "legacy2", "name": "b", "guild_id": None},             # guild_id explicitement vide : à migrer
    {"id": "already", "name": "c", "guild_id": OTHER_A},          # déjà une guilde : ne doit JAMAIS être touché
])
SHARED.write_text(json.dumps({OTHER_A: ["un-son-deja-partage-avant-la-migration"]}), encoding="utf-8")

r = run("--native-guild-id", NATIVE, "--share-to", f"{OTHER_A},{OTHER_B}")
check("aperçu (sans --apply) : rien n'est écrit", not CATALOG.read_text(encoding="utf-8").count('"guild_id": "' + NATIVE), r.stdout)
check("aperçu : annonce 2 sons migrés, 1 déjà présent", "2 migré(s)" in r.stdout and "1 son(s) avaient déjà un guild_id" in r.stdout, r.stdout)

run("--native-guild-id", NATIVE, "--share-to", f"{OTHER_A},{OTHER_B}", "--apply")
cat = read_catalog()
by_id = {e["id"]: e for e in cat}
check("les 2 sons sans guilde reçoivent la guilde native", by_id["legacy1"]["guild_id"] == NATIVE and by_id["legacy2"]["guild_id"] == NATIVE)
check("le son qui avait déjà une guilde N'EST PAS touché", by_id["already"]["guild_id"] == OTHER_A)

shared = read_shared()
check("partagé vers les 2 guildes cibles, les 2 sons migrés (pas 'already')",
      set(shared[OTHER_A]) >= {"legacy1", "legacy2"} and "already" not in shared[OTHER_A]
      and set(shared[OTHER_B]) >= {"legacy1", "legacy2"}, shared)
check("l'entrée déjà présente avant la migration est conservée (union, pas écrasement)",
      "un-son-deja-partage-avant-la-migration" in shared[OTHER_A], shared)
check("aucune fuite vers une guilde non listée dans --share-to", FOREIGN not in shared)

backups = list(DATA.glob("sounds_data/catalog.json.bak-*")) + list(DATA.glob("shared_categories.json.bak-*"))
check("une sauvegarde horodatée de chaque fichier a été créée avant écriture", len(backups) == 2, backups)

# ---------- Idempotence ----------
before_cat, before_shared = read_catalog(), read_shared()
r2 = run("--native-guild-id", NATIVE, "--share-to", f"{OTHER_A},{OTHER_B}", "--apply")
check("rejouer : annonce 0 migration, 0 partage, rien écrit", "0 migré(s)" in r2.stdout and "Rien à écrire" in r2.stdout, r2.stdout)
check("rejouer : contenu strictement identique (pas de doublon, pas de réordonnancement destructeur)",
      read_catalog() == before_cat and read_shared() == before_shared)
backups_after = list(DATA.glob("sounds_data/catalog.json.bak-*")) + list(DATA.glob("shared_categories.json.bak-*"))
check("rejouer sans rien à écrire : aucune sauvegarde superflue créée", len(backups_after) == 2)

# ---------- Erreurs ----------
r3 = run("--native-guild-id", NATIVE, "--share-to", f"{NATIVE},{OTHER_A}", expect_ok=False)
check("mettre la guilde native dans --share-to est refusé (redondant, signe d'une erreur de saisie)", r3.returncode != 0, r3.stderr)

write_catalog([{"id": "x", "name": "x"}])
SHARED.unlink(missing_ok=True)
r4 = run("--native-guild-id", NATIVE, "--share-to", "")
check("sans --share-to : fonctionne quand même (juste le rattachement natif)", r4.returncode == 0 and "1 migré(s)" in r4.stdout, r4.stdout)
run("--native-guild-id", NATIVE, "--share-to", "", "--apply")
check("sans --share-to : shared_categories.json n'est même pas créé", not SHARED.exists())

missing = DATA / "nonexistent"
r5 = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(missing), "--native-guild-id", NATIVE], capture_output=True, text=True)
check("dossier de données inexistant : échec propre, pas de traceback", r5.returncode != 0 and "Traceback" not in r5.stderr, r5.stderr)

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
