"""Éditeur de sons de la page web : les routes qu'il appelle (envoi, modification, remplacement du fichier, catégories partagées,
suppression, lecture du fichier) fonctionnent avec la SEULE session Discord de la page (pas de secret partagé), depuis son origine,
avec de vrais envois de fichiers (multipart) vers le vrai serveur HTTP de server.py, et les droits sont ceux du serveur.
Faux objets Discord. Dossier de données isolé (_env.py)."""
import asyncio
import hashlib
import io
import json
import math
import shutil
import struct
import sys
import time
import wave

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import aiohttp  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


# ---------- faux Discord ----------
class Perms:
    def __init__(self, administrator=False): self.administrator = administrator

class Member:
    def __init__(self, uid, administrator=False):
        self.id, self.guild_permissions, self.roles, self.display_name = uid, Perms(administrator), [], f"user{uid}"

class Guild:
    def __init__(self, gid, owner_id, name):
        self.id, self.owner_id, self.roles, self.name, self.icon, self.members = gid, owner_id, [], name, None, {}
    def get_member(self, uid): return self.members.get(uid)
    def get_role(self, rid): return None

G1, G2 = Guild(3101, 1, "Alpha"), Guild(3102, 2, "Beta")   # 1 = propriétaire (donc admin) de Alpha, 2 = de Beta
for guild, members in ((G1, (1, 20, 21)), (G2, (2, 20, 21))):
    for uid in members:
        guild.members[uid] = Member(uid)
GUILDS = [G1, G2]
G1_ID, G2_ID = str(G1.id), str(G2.id)
bot = S.bot


async def memberships(user_id):
    return [(g, g.members[user_id]) for g in GUILDS if user_id in g.members]

bot.find_memberships = memberships
bot.get_guild = lambda gid: next((g for g in GUILDS if g.id == gid), None)

PUBLIC = "https://waseboard.test"
S.SHARED_SECRET = "s" * 40
S.PUBLIC_URL = PUBLIC
S.HTTP_HOST, S.HTTP_PORT = "127.0.0.1", 0
S.SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
S.TRASH_DIR.mkdir(exist_ok=True)

TOKENS = {}
for uid in (1, 2, 20, 21):
    TOKENS[uid] = f"web-token-{uid}-" + "x" * 24
    bot.oauth_sessions[TOKENS[uid]] = {"user_id": str(uid), "username": f"user{uid}", "avatar_url": None, "discord_access_token": "x",
                                       "discord_refresh_token": "y", "discord_token_expires_at": time.time() + 10 ** 6, "created_at": 0, "last_used_at": 0}


def wav_bytes(seconds=1.0, hz=440):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / 48000))) for i in range(int(48000 * seconds))))
    return buffer.getvalue()


def headers(uid, origin=PUBLIC):
    result = {"Authorization": f"Bearer {TOKENS[uid]}"}
    if origin:
        result["Origin"] = origin
    return result


def multipart(fields, filename, content):
    form = aiohttp.FormData()
    for key, value in fields:
        form.add_field(key, value)
    form.add_field("file", content, filename=filename, content_type="application/octet-stream")
    return form


async def main():
    await bot._start_http_server()
    base = f"http://127.0.0.1:{bot._web_runner.addresses[0][1]}"
    check("la page web est active (public_url valide)", bot.web_app is not None and bot.web_app.enabled)
    async with aiohttp.ClientSession() as http:
        async def call(method, path, uid=None, origin=PUBLIC, **kwargs):
            h = headers(uid, origin) if uid else ({"Origin": origin} if origin else {})
            async with http.request(method, base + path, headers=h, **kwargs) as response:
                raw = await response.read()
                try:
                    return response.status, json.loads(raw)
                except Exception:
                    return response.status, raw

        print("\n--- Sans session, ou depuis un autre site : refusé ---")
        audio = wav_bytes(1.0)
        fields = [("name", "Test"), ("guild_id", G1_ID), ("emoji", "😂")]
        st, _ = await call("POST", "/sounds", data=multipart(fields, "a.wav", audio))
        check("envoi sans session : 401", st == 401, st)
        st, _ = await call("POST", "/sounds", uid=20, origin="https://evil.example", data=multipart(fields, "a.wav", audio))
        check("envoi avec une session mais depuis un autre site : 401", st == 401, st)
        st, p = await call("GET", "/sounds", uid=20, origin=None)
        check("lecture du catalogue avec la session seule (sans Origin : lecture de même origine) : 200", st == 200 and p == {"sounds": []}, (st, p))

        print("\n--- Ajout ---")
        fields = [("name", "Rire"), ("guild_id", G1_ID), ("emoji", "😂"), ("trim_start_ms", "100"), ("trim_end_ms", "700")]
        st, added = await call("POST", "/sounds", uid=20, data=multipart(fields, "rire.wav", audio))
        sound_id = added.get("id") if isinstance(added, dict) else None
        check("envoi par la page : 200, son créé avec son emoji et sa découpe", st == 200 and added["name"] == "Rire" and added["emoji"] == "😂"
              and added["trim_start_ms"] == 100 and added["trim_end_ms"] == 700 and added["guild_id"] == G1_ID, (st, added))
        check("…auteur reconnu, droits d'édition", added["uploaded_by"] == "20" and added["is_mine"] is True and added["can_edit"] is True)
        check("…le contenu est stocké tel quel (empreinte calculée par le serveur)", (S.SOUNDS_DIR / f"{sound_id}.wav").read_bytes() == audio
              and added["hash"] == hashlib.sha256(audio).hexdigest())
        st, p = await call("POST", "/sounds", uid=20, data=multipart(fields, "virus.exe", b"MZ"))
        check("extension refusée : 400 lisible", st == 400 and "extension" in p["error"], (st, p))
        st, p = await call("POST", "/sounds", uid=20, data=multipart([("name", "X"), ("guild_id", "999"), ("emoji", "😂")], "a.wav", audio))
        check("serveur dont on n'est pas membre : 403", st == 403, (st, p))
        st, p = await call("POST", "/sounds", uid=20, data=multipart([("name", "X"), ("guild_id", G1_ID), ("emoji", "😂"), ("trim_start_ms", "700"), ("trim_end_ms", "100")], "a.wav", audio))
        check("découpe incohérente : 400", st == 400, (st, p))
        S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS, "upload_admins_only": True})
        st, p = await call("POST", "/sounds", uid=21, data=multipart(fields, "a.wav", audio))
        check("serveur réservé aux admins : un membre reçoit 403 avec le motif", st == 403 and "administrateurs" in p["error"], (st, p))
        st, p = await call("GET", "/my-guilds", uid=21)
        check("/my-guilds indique où l'on peut ajouter (la page masque alors le bouton « + »)", {g["id"]: g["can_upload"] for g in p["guilds"]} == {G1_ID: False, G2_ID: True}, p)
        S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS})

        print("\n--- Lecture et droits d'édition ---")
        st, p = await call("GET", "/sounds", uid=20)
        mine = next(s for s in p["sounds"] if s["id"] == sound_id)
        check("l'auteur voit can_edit et is_mine", mine["can_edit"] and mine["is_mine"])
        st, p = await call("GET", "/sounds", uid=21)
        other = next(s for s in p["sounds"] if s["id"] == sound_id)
        check("un autre membre le voit mais ne peut pas le modifier", not other["can_edit"] and not other["is_mine"])
        st, p = await call("GET", "/sounds", uid=1)
        check("l'admin du serveur peut le modifier", next(s for s in p["sounds"] if s["id"] == sound_id)["can_edit"])
        st, data = await call("GET", f"/sounds/{sound_id}/file", uid=21)
        check("fichier d'un son : lisible par la page (forme d'onde de l'éditeur)", st == 200 and data == audio, st)

        print("\n--- Modification (PATCH) ---")
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=21, json={"name": "Piraté"})
        check("un membre ordinaire ne peut pas renommer : 403", st == 403, (st, p))
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=20, json={"name": "Gros rire", "emoji": "🤣", "trim_start_ms": 200, "trim_end_ms": 900})
        check("l'auteur renomme, change l'emoji et la découpe", st == 200 and p["name"] == "Gros rire" and p["emoji"] == "🤣" and p["trim_start_ms"] == 200 and p["trim_end_ms"] == 900, (st, p))
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=20, json={"trim_start_ms": None, "trim_end_ms": None})
        check("découpe retirée (deux valeurs nulles) : le son entier est de retour", st == 200 and "trim_start_ms" not in p and "trim_end_ms" not in p, (st, p))
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=20, json={"name": "   "})
        check("nom vide : 400", st == 400)
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=20, json={"trim_start_ms": 900, "trim_end_ms": 200})
        check("découpe incohérente : 400, rien de modifié", st == 400 and S.load_catalog()[0]["name"] == "Gros rire")
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=1, json={"name": "Renommé par l'admin"})
        check("l'admin du serveur peut renommer le son d'un autre", st == 200 and p["name"] == "Renommé par l'admin", (st, p))
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=20, json={"name": "Gros rire"})
        check("…et l'auteur reprend la main", st == 200)

        print("\n--- Nouveau fichier (PUT /sounds/{id}/file) ---")
        newer = wav_bytes(2.0, 880)
        st, p = await call("PUT", f"/sounds/{sound_id}/file", uid=21, data=multipart([], "autre.wav", newer))
        check("un membre ordinaire ne peut pas remplacer : 403", st == 403, (st, p))
        st, p = await call("PATCH", f"/sounds/{sound_id}", uid=20, json={"trim_start_ms": 100, "trim_end_ms": 600})
        st, p = await call("PUT", f"/sounds/{sound_id}/file", uid=20, data=multipart([], "plus-long.wav", newer))
        check("l'auteur remplace le fichier : même identité, nouveau contenu, découpe retirée",
              st == 200 and p["id"] == sound_id and p["hash"] == hashlib.sha256(newer).hexdigest() and "trim_start_ms" not in p
              and (S.SOUNDS_DIR / f"{sound_id}.wav").read_bytes() == newer, (st, p))
        st, p = await call("PUT", f"/sounds/{sound_id}/file", uid=20, data=multipart([], "plus-long.wav", newer))
        check("même contenu : « unchanged », rien n'est touché", st == 200 and p.get("unchanged") is True, (st, p))
        st, p = await call("PUT", f"/sounds/{sound_id}/file", uid=20, data=multipart([], "x.txt", b"zzz"))
        check("extension refusée au remplacement : 400", st == 400, (st, p))

        print("\n--- Catégories partagées des serveurs ---")
        st, p = await call("POST", "/shared-categories/sounds", uid=21, json={"guild_id": G2_ID, "sound_id": sound_id, "action": "add"})
        check("un membre ordinaire ne partage pas le son d'un autre : 403", st == 403, (st, p))
        st, p = await call("POST", "/shared-categories/sounds", uid=20, json={"guild_id": G2_ID, "sound_id": sound_id, "action": "add"})
        check("l'auteur le partage dans un autre de ses serveurs", st == 200 and sound_id in p["sound_ids"], (st, p))
        st, p = await call("GET", f"/shared-categories?guild_id={G2_ID}", uid=21)
        check("…et les membres de ce serveur le voient", st == 200 and sound_id in p["sound_ids"])
        st, p = await call("GET", "/sounds", uid=2)
        check("…y compris quelqu'un qui n'est QUE dans ce serveur (propriétaire de Beta)", st == 200 and any(s["id"] == sound_id for s in p["sounds"]), p if st != 200 else "")
        st, p = await call("POST", "/shared-categories/sounds", uid=20, json={"guild_id": G2_ID, "sound_id": sound_id, "action": "remove"})
        check("l'auteur le retire", st == 200 and sound_id not in p["sound_ids"], (st, p))
        st, p = await call("POST", "/shared-categories/sounds", uid=20, json={"guild_id": "999", "sound_id": sound_id, "action": "add"})
        check("serveur inconnu : 403", st == 403, (st, p))

        print("\n--- Suppression ---")
        st, p = await call("DELETE", f"/sounds/{sound_id}", uid=21)
        check("un membre ordinaire ne supprime pas : 403, le son reste", st == 403 and any(s["id"] == sound_id for s in S.load_catalog()), (st, p))
        st, p = await call("DELETE", f"/sounds/{sound_id}", uid=20)
        check("l'auteur supprime : 200, le son quitte le catalogue", st == 200 and not any(s["id"] == sound_id for s in S.load_catalog()), (st, p))
        check("…il va à la corbeille (un admin peut le restaurer)", any(r["id"] == sound_id for r in S.load_trash()))
        st, p = await call("GET", "/sounds", uid=20)
        check("…et la page ne le voit plus", st == 200 and p == {"sounds": []}, p)
        st, p = await call("DELETE", f"/sounds/{sound_id}", uid=20)
        check("supprimer ce qui n'existe plus : 404", st == 404, (st, p))

    await bot._web_runner.cleanup()


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
