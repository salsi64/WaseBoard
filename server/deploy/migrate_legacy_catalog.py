#!/usr/bin/env python3
"""Migration ponctuelle (bascule prod) : rattache les sons uploadés AVANT l'isolation par guilde (sans
champ `guild_id`) à une guilde « native » choisie, et les partage explicitement vers d'autres guildes
(shared_categories.json) pour que rien ne devienne invisible à la bascule. Voir CUTOVER.md.

Usage :
    python3 migrate_legacy_catalog.py --data-dir /chemin/vers/les/donnees \
        --native-guild-id 329938187025776642 \
        --share-to 326491598869495809,690909467436384308,1344729394249338985,1554898433649807441
        [--apply]   (sans --apply : aperçu seul, rien n'est écrit)

Sans risque à rejouer plusieurs fois : un son qui a déjà un guild_id n'est jamais modifié, et le partage
est une UNION (rien n'est retiré de ce qui existe déjà dans shared_categories.json — y compris les
associations posées par l'ancienne fonctionnalité de catégorisation par serveur, déjà présente avant
cette migration).
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", required=True, type=Path, help="dossier de données du serveur (contient sounds_data/)")
    p.add_argument("--native-guild-id", required=True, help="guilde propriétaire des sons sans guild_id")
    p.add_argument("--share-to", default="", help="IDs de guilde séparés par des virgules, en plus de la native")
    p.add_argument("--apply", action="store_true", help="écrit réellement les fichiers (sinon aperçu seul)")
    args = p.parse_args()

    catalog_path = args.data_dir / "sounds_data" / "catalog.json"
    shared_path = args.data_dir / "shared_categories.json"
    if not catalog_path.exists():
        print(f"catalogue introuvable : {catalog_path}", file=sys.stderr)
        return 1

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    native = str(args.native_guild_id)
    share_targets = [g.strip() for g in args.share_to.split(",") if g.strip()]
    if native in share_targets:
        print(f"--native-guild-id ({native}) n'a pas besoin d'être dans --share-to : déjà visible nativement.", file=sys.stderr)
        return 1

    migrated_ids = []
    for entry in catalog:
        if not entry.get("guild_id"):
            entry["guild_id"] = native
            migrated_ids.append(entry["id"])

    shared = json.loads(shared_path.read_text(encoding="utf-8")) if shared_path.exists() else {}
    added_per_guild = {}
    for gid in share_targets:
        existing = set(shared.get(gid, []))
        before = len(existing)
        existing.update(migrated_ids)
        shared[gid] = sorted(existing)
        added_per_guild[gid] = len(existing) - before

    print(f"Catalogue : {len(catalog)} son(s) au total, {len(migrated_ids)} migré(s) vers la guilde native {native}.")
    print(f"{len(catalog) - len(migrated_ids)} son(s) avaient déjà un guild_id : inchangés.")
    for gid in share_targets:
        print(f"Partagé vers {gid} : {added_per_guild[gid]} nouveau(x) (total {len(shared[gid])} son(s) pour cette guilde).")

    if not args.apply:
        print("\n(aperçu seul — rien n'a été écrit ; relancez avec --apply pour appliquer)")
        return 0

    if not migrated_ids and not any(added_per_guild.values()):
        print("\nRien à écrire (déjà migré).")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backups = []

    shutil.copy2(catalog_path, catalog_path.with_name(f"catalog.json.bak-{stamp}"))
    backups.append(f"catalog.json.bak-{stamp}")
    tmp = catalog_path.with_name(catalog_path.name + ".tmp")
    tmp.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(catalog_path)

    # shared_categories.json : on n'y touche que s'il y a au moins une guilde cible — sans --share-to, le
    # fichier reste tel quel (jamais créé vide s'il n'existait pas avant).
    if share_targets:
        if shared_path.exists():
            shutil.copy2(shared_path, shared_path.with_name(f"shared_categories.json.bak-{stamp}"))
            backups.append(f"shared_categories.json.bak-{stamp}")
        tmp2 = shared_path.with_name(shared_path.name + ".tmp")
        tmp2.write_text(json.dumps(shared, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp2.replace(shared_path)

    print(f"\nÉcrit. Sauvegardes : {', '.join(backups)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
