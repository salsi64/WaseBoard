"""Remplacement du fichier d'un son (PUT /sounds/{id}/file) : vrai code du handler de server.py, faux objets Discord.
Lancé via tests/run_all.sh : dossier de données isolé et jetable créé par _env.py, rien du vrai déploiement n'est touché."""
import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
import time
import wave

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


# ---------- faux Discord (mêmes objets minimalistes que test_roles.py) ----------
class Perms:
    def __init__(self, administrator=False): self.administrator = administrator

class Role:
    def __init__(self, rid, position=1, managed=False, default=False):
        self.id, self.position, self.managed, self.name, self._default = rid, position, managed, f"role{rid}", default
    def is_default(self): return self._default

class Member:
    def __init__(self, uid, administrator=False, roles=()):
        self.id = uid
        self.guild_permissions = Perms(administrator)
        self.roles = list(roles)
        self.display_name = f"user{uid}"

class Guild:
    def __init__(self, gid, owner_id, roles):
        self.id, self.owner_id, self.roles, self.name, self.icon = gid, owner_id, roles, f"guild{gid}", None
        self.members = {}
    def get_member(self, uid): return self.members.get(uid)
    def get_role(self, rid): return next((r for r in self.roles if r.id == rid), None)


G1 = Guild(1001, owner_id=1, roles=[Role(1001, 0, default=True)])
G2 = Guild(1002, owner_id=2, roles=[Role(1002, 0, default=True)])
for uid, kw in [(1, {}), (10, {"administrator": True}), (20, {}), (21, {})]:
    G1.members[uid] = Member(uid, **kw)
for uid in (2, 30):
    G2.members[uid] = Member(uid)
GUILDS = [G1, G2]

bot = S.bot


async def fake_memberships(user_id):
    return [(g, g.members[user_id]) for g in GUILDS if user_id in g.members]

bot.find_memberships = fake_memberships
bot.get_guild = lambda gid: next((g for g in GUILDS if g.id == gid), None)

for uid in (1, 10, 20, 21, 2, 30):
    bot.oauth_sessions[f"tok{uid}"] = {
        "user_id": str(uid), "username": f"user{uid}", "avatar_url": None,
        "discord_access_token": "x", "discord_refresh_token": "y",
        "discord_token_expires_at": time.time() + 10**6, "created_at": 0, "last_used_at": 0,
    }


class Upload:
    def __init__(self, filename, data):
        self.filename, self.file = filename, io.BytesIO(data)

class Req:
    def __init__(self, uid, body=None, match=None, form=None):
        self.headers = {"X-WaseBoard-Token": S.SHARED_SECRET, "Authorization": f"Bearer tok{uid}"}
        self.query, self.match_info, self._body, self._form = {}, match or {}, body, form
    async def json(self): return self._body
    async def post(self): return self._form


async def call(handler, uid, body=None, match=None, form=None):
    resp = await handler(Req(uid, body, match, form))
    try:
        payload = json.loads(resp.text)
    except Exception:
        payload = None
    return resp.status, payload


def tiny_wav(seconds: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * seconds)
    return buf.getvalue()


def blob(kb: int) -> bytes:
    return b"RIFF" + os.urandom(kb * 1024)


def settings_set(gid, **kw):
    s = S.get_guild_settings(gid); s.update(kw); S.save_guild_settings(gid, s)


def reset_catalog():
    for f in S.SOUNDS_DIR.iterdir():
        if f.is_file():
            f.unlink()
    S.save_catalog([])


async def upload(uid, name, data, filename="a.wav"):
    st, p = await call(bot._handle_upload_sound, uid, form={"name": name, "guild_id": "1001", "file": Upload(filename, data)})
    assert st == 200, (st, p)
    return p["id"]


async def replace(uid, sound_id, data, filename="b.wav"):
    return await call(bot._handle_replace_sound_file, uid, match={"id": sound_id}, form={"file": Upload(filename, data)})


def entry_of(sound_id):
    return next((s for s in S.load_catalog() if s["id"] == sound_id), None)


def audit_actions():
    return [r["action"] for r in S._read_jsonl(S.AUDIT_PATH)]


async def main():
    G1_ID = "1001"
    reset_catalog()

    print("\n--- Remplacement nominal ---")
    old = tiny_wav(2)
    sid = await upload(20, "explosion", old)
    await call(bot._handle_update_sound, 20, {"trim_start_ms": 200, "trim_end_ms": 1500, "emoji": "💥"}, match={"id": sid})
    before = entry_of(sid)
    check("préparation : le son est découpé et a un emoji", before["trim_start_ms"] == 200 and before["emoji"] == "💥", before)

    new = tiny_wav(3)
    st, p = await replace(20, sid, new)
    check("l'auteur remplace le fichier de son propre son -> 200", st == 200, (st, p))
    after = entry_of(sid)
    check("le fichier sur disque est le nouveau", (S.SOUNDS_DIR / f"{sid}.wav").read_bytes() == new)
    check("le hash est celui du nouveau contenu", after["hash"] == hashlib.sha256(new).hexdigest() and p["hash"] == after["hash"], after)
    check("la découpe (visait l'ancien fichier) est retirée", "trim_start_ms" not in after and "trim_end_ms" not in after, after)
    check("identité conservée : id, nom, emoji, auteur, date d'ajout, guilde",
          all(after[k] == before[k] for k in ("id", "name", "emoji", "uploaded_by", "uploaded_at", "guild_id")), after)
    check("replaced_at est renseigné", isinstance(after.get("replaced_at"), int) and after["replaced_at"] >= before["uploaded_at"], after)
    check("aucun fichier temporaire résiduel", not list(S.SOUNDS_DIR.glob("*.replacing*")), list(S.SOUNDS_DIR.iterdir()))
    check("le journal trace le remplacement", "replace" in audit_actions(), audit_actions())

    # Compatibilité avec les clients qui ne sont pas à jour : un son remplacé garde TOUS ses champs historiques et ne
    # gagne que `replaced_at` (un ancien client ignore les champs inconnus).
    LEGACY_KEYS = {"id", "name", "extension", "hash", "guild_id", "uploaded_by", "uploaded_at", "is_mine", "can_edit",
                   "emoji", "trim_start_ms", "trim_end_ms"}
    st, listing = await call(bot._handle_list_sounds, 20)
    listed = next(x for x in listing["sounds"] if x["id"] == sid)
    check("compat anciens clients : champs historiques tous présents après remplacement",
          {"id", "name", "extension", "hash", "guild_id", "uploaded_by", "is_mine", "can_edit"} <= set(listed), set(listed))
    check("compat anciens clients : seul `replaced_at` est ajouté", set(listed) - LEGACY_KEYS == {"replaced_at"}, set(listed) - LEGACY_KEYS)
    check("compat anciens clients : l'extension du catalogue est celle du fichier réellement servi",
          (S.SOUNDS_DIR / f"{sid}{listed['extension']}").exists())

    print("\n--- Même contenu : rien à faire ---")
    ts = entry_of(sid)["replaced_at"]
    st, p = await replace(20, sid, new)
    check("même contenu -> 200 'unchanged', rien n'est réécrit", st == 200 and p.get("unchanged") is True and entry_of(sid)["replaced_at"] == ts, (st, p))
    await call(bot._handle_update_sound, 20, {"trim_start_ms": 100, "trim_end_ms": 900}, match={"id": sid})
    st, p = await replace(20, sid, new)
    check("même contenu : la découpe choisie est conservée", st == 200 and entry_of(sid)["trim_start_ms"] == 100, entry_of(sid))

    print("\n--- Changement d'extension ---")
    mp3 = b"ID3" + os.urandom(2048)
    st, p = await replace(20, sid, mp3, "nouveau.mp3")
    check("wav -> mp3 : 200 et extension mise à jour", st == 200 and p["extension"] == ".mp3" and entry_of(sid)["extension"] == ".mp3", (st, p))
    check("le nouveau fichier existe, l'ancien .wav est supprimé",
          (S.SOUNDS_DIR / f"{sid}.mp3").read_bytes() == mp3 and not (S.SOUNDS_DIR / f"{sid}.wav").exists(), list(S.SOUNDS_DIR.iterdir()))

    print("\n--- Droits ---")
    st, _ = await replace(21, sid, tiny_wav(1))
    check("un autre membre (ni auteur ni admin) -> 403", st == 403, st)
    st, _ = await replace(30, sid, tiny_wav(1))
    check("non-membre du serveur -> 404 (le son n'existe pas pour lui)", st == 404, st)
    st, _ = await replace(10, sid, tiny_wav(1))
    check("un admin du serveur peut remplacer le son d'un autre", st == 200, st)
    st, _ = await replace(1, sid, tiny_wav(2))
    check("le propriétaire du serveur aussi", st == 200, st)

    settings_set(G1_ID, upload_admins_only=True)
    st, p = await replace(20, sid, tiny_wav(3))
    check("ajouts réservés aux admins : l'auteur ordinaire ne contourne pas la règle -> 403", st == 403 and "droit" in p["error"], (st, p))
    st, _ = await replace(10, sid, tiny_wav(3))
    check("... mais un admin passe", st == 200, st)
    settings_set(G1_ID, upload_admins_only=False, blocked_uploaders=["20"])
    st, p = await replace(20, sid, tiny_wav(4))
    check("membre bloqué : ne peut pas remplacer non plus -> 403", st == 403, (st, p))
    settings_set(G1_ID, blocked_uploaders=[])

    print("\n--- Entrées invalides ---")
    st, _ = await call(bot._handle_replace_sound_file, 20, match={"id": sid}, form={})
    check("pas de champ 'file' -> 400", st == 400, st)
    st, _ = await replace(20, sid, tiny_wav(1), "a.exe")
    check("extension interdite -> 400", st == 400, st)
    st, _ = await replace(20, "nexistepas", tiny_wav(1))
    check("son inconnu -> 404", st == 404, st)
    sid_del = await upload(20, "à supprimer", tiny_wav(1))
    await call(bot._handle_delete_sound, 20, match={"id": sid_del})
    st, _ = await replace(20, sid_del, tiny_wav(2))
    check("son à la corbeille -> 404", st == 404, st)

    print("\n--- Plafonds (le refus ne doit rien casser) ---")
    reset_catalog()
    sid = await upload(20, "limité", tiny_wav(2))
    before_bytes, before_hash = (S.SOUNDS_DIR / f"{sid}.wav").read_bytes(), entry_of(sid)["hash"]

    def untouched():
        return (S.SOUNDS_DIR / f"{sid}.wav").read_bytes() == before_bytes and entry_of(sid)["hash"] == before_hash \
            and not list(S.SOUNDS_DIR.glob("*.replacing*"))

    settings_set(G1_ID, max_file_mb=1)
    st, p = await replace(20, sid, blob(1100))
    check("fichier au-dessus de max_file_mb -> 413, ancien fichier intact", st == 413 and untouched(), (st, p))
    settings_set(G1_ID, max_file_mb=0)

    if shutil.which("ffprobe"):
        settings_set(G1_ID, max_duration_s=2)
        st, p = await replace(20, sid, tiny_wav(5))
        check("son plus long que max_duration_s -> 413, ancien fichier intact, temporaire nettoyé", st == 413 and untouched(), (st, p))
        st, _ = await replace(20, sid, tiny_wav(1))
        check("un son assez court passe", st == 200, st)
        settings_set(G1_ID, max_duration_s=0)
    else:
        print("SKIP  durée maximale (ffprobe absent)")

    reset_catalog()
    sid_a = await upload(20, "A", blob(600))
    sid_b = await upload(20, "B", blob(300))
    settings_set(G1_ID, max_total_mb=1)  # 1024 Ko ; 600 + 300 = 900 Ko déjà utilisés
    st, p = await replace(20, sid_a, blob(700))
    check("espace : l'ancien fichier est libéré (900 - 600 + 700 = 1000 Ko <= 1024) -> 200", st == 200, (st, p))
    before_bytes, before_hash = (S.SOUNDS_DIR / f"{sid_a}.wav").read_bytes(), entry_of(sid_a)["hash"]
    st, p = await replace(20, sid_a, blob(900))
    check("espace : 1000 - 700 + 900 = 1200 Ko > 1024 -> 413, ancien fichier intact",
          st == 413 and (S.SOUNDS_DIR / f"{sid_a}.wav").read_bytes() == before_bytes and entry_of(sid_a)["hash"] == before_hash
          and not list(S.SOUNDS_DIR.glob("*.replacing*")), (st, p))
    settings_set(G1_ID, max_total_mb=0)
    check("le nombre de sons n'a pas changé", len(S.load_catalog()) == 2, len(S.load_catalog()))

    print("\n--- Limitation de débit ---")
    class R:
        def __init__(self, method, path): self.method, self.path = method, path
    check("PUT /sounds/<id>/file partage le seau d'upload", S.rate_limit_bucket(R("PUT", "/sounds/abc/file")) == "upload")
    check("POST /sounds : inchangé", S.rate_limit_bucket(R("POST", "/sounds")) == "upload")
    check("PATCH /sounds/<id> et GET .../file : non limités", S.rate_limit_bucket(R("PATCH", "/sounds/abc")) is None
          and S.rate_limit_bucket(R("GET", "/sounds/abc/file")) is None)


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir()
for leftover in (S.GUILD_SETTINGS_PATH, S.AUDIT_PATH, S.STATS_PATH, S.SHARED_CATEGORIES_PATH):
    leftover.unlink(missing_ok=True)
asyncio.run(main())
print("\nRÉSULTAT :", "TOUT PASSE" if all(results) else f"{results.count(False)} échec(s) sur {len(results)}", f"({len(results)} vérifications)")
sys.exit(0 if all(results) else 1)
