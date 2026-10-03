"""Matrice de droits du jalon 4 : vrai code des handlers de server.py, faux objets Discord.
Lancé via tests/run_all.sh : dossier de données isolé et jetable créé par _env.py, rien du vrai
déploiement (catalogue, corbeille, réglages, journaux) n'est jamais touché."""
import asyncio
import io
import json
import shutil
import sys
import time
import wave
from pathlib import Path

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


# ---------- faux Discord ----------
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


ROLE_MOD = Role(555, position=5)
G1 = Guild(1001, owner_id=1, roles=[Role(1001, 0, default=True), ROLE_MOD, Role(556, 4), Role(900, 3, managed=True)])
G2 = Guild(1002, owner_id=2, roles=[Role(1002, 0, default=True)])
for uid, kw in [(1, {}), (10, {"administrator": True}), (11, {"roles": [ROLE_MOD]}), (20, {}), (21, {}), (22, {})]:
    G1.members[uid] = Member(uid, **kw)
for uid, kw in [(2, {}), (22, {}), (30, {}), (31, {"administrator": True})]:
    G2.members[uid] = Member(uid, **kw)
GUILDS = [G1, G2]

bot = S.bot


async def fake_memberships(user_id):
    return [(g, g.members[user_id]) for g in GUILDS if user_id in g.members]

bot.find_memberships = fake_memberships
bot.get_guild = lambda gid: next((g for g in GUILDS if g.id == gid), None)

async def _noop(*a, **k): return None
bot._record_play_activity = _noop
FF_CALLS = []
S.discord.FFmpegPCMAudio = lambda path, **kw: (FF_CALLS.append((path, kw)), ("ffmpeg", path))[1]
S.discord.PCMVolumeTransformer = lambda src, volume=1.0: ("vol", src)
S.record_play_stat = lambda *a, **k: None

for uid in (1, 10, 11, 20, 21, 22, 2, 30, 31):
    bot.oauth_sessions[f"tok{uid}"] = {
        "user_id": str(uid), "username": f"user{uid}", "avatar_url": None,
        "discord_access_token": "x", "discord_refresh_token": "y",
        "discord_token_expires_at": time.time() + 10**6, "created_at": 0, "last_used_at": 0,
    }


class Upload:
    def __init__(self, filename, data):
        self.filename, self.file = filename, io.BytesIO(data)

class Req:
    def __init__(self, uid, body=None, match=None, query=None, form=None):
        self.headers = {"X-WaseBoard-Token": S.SHARED_SECRET, "Authorization": f"Bearer tok{uid}"}
        self.query, self.match_info, self._body, self._form = query or {}, match or {}, body, form
    async def json(self): return self._body
    async def post(self): return self._form


async def call(handler, uid, body=None, match=None, query=None, form=None):
    resp = await handler(Req(uid, body, match, query, form))
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


def settings_set(gid, **kw):
    s = S.get_guild_settings(gid); s.update(kw); S.save_guild_settings(gid, s)


def upload_form(guild_id, name="son", data=b"RIFFfake", filename="a.wav"):
    return {"name": name, "guild_id": str(guild_id), "file": Upload(filename, data)}


async def main():
    G1_ID, G2_ID = str(G1.id), str(G2.id)

    # Un ancien son (sans auteur) et un son natif de G2, écrits à la main comme le ferait l'existant
    legacy = {"id": "legacy1", "name": "ancien", "extension": ".wav", "hash": "h0", "guild_id": G1_ID}
    (S.SOUNDS_DIR / "legacy1.wav").write_bytes(b"x")
    S.save_catalog([legacy])

    print("\n--- Upload ---")
    st, p = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID, "sonA"))
    check("membre ordinaire uploade (ouvert par défaut)", st == 200 and p["uploaded_by"] == "20" and p["is_mine"], (st, p))
    sound_a = p["id"]
    st, _ = await call(bot._handle_upload_sound, 30, form=upload_form(G1_ID))
    check("non-membre de la guilde -> 403", st == 403, st)
    st, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID, filename="a.exe"))
    check("extension interdite -> 400", st == 400, st)

    settings_set(G1_ID, upload_admins_only=True)
    st, p = await call(bot._handle_upload_sound, 21, form=upload_form(G1_ID))
    check("upload restreint : membre -> 403 avec message", st == 403 and "administrateurs" in p["error"], (st, p))
    st, p = await call(bot._handle_upload_sound, 10, form=upload_form(G1_ID, "adm"))
    check("upload restreint : admin Discord passe", st == 200, (st, p))
    st, p = await call(bot._handle_upload_sound, 1, form=upload_form(G1_ID, "own"))
    check("upload restreint : propriétaire passe", st == 200, (st, p))
    settings_set(G1_ID, upload_admins_only=False, blocked_uploaders=["21"])
    st, p = await call(bot._handle_upload_sound, 21, form=upload_form(G1_ID))
    check("membre bloqué -> 403", st == 403 and "retiré" in p["error"], (st, p))
    st, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID, "autre"))
    check("un autre membre n'est pas affecté par le blocage", st == 200, st)
    settings_set(G1_ID, blocked_uploaders=[])

    count_g1 = sum(1 for e in S.load_catalog() if e["guild_id"] == G1_ID)
    settings_set(G1_ID, max_sounds=count_g1)
    st, p = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID))
    check("quota de sons atteint -> 403", st == 403 and "limite" in p["error"], (st, p))
    settings_set(G1_ID, max_sounds=0, max_file_mb=1)
    st, p = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID, data=b"0" * (2 * 1024 * 1024)))
    check("fichier trop gros -> 413", st == 413, (st, p))
    if shutil.which("ffprobe"):
        settings_set(G1_ID, max_file_mb=0, max_duration_s=1)
        st, p = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID, data=tiny_wav(3), filename="long.wav"))
        check("son trop long (ffprobe) -> 413", st == 413, (st, p))
        orphan = [f for f in S.SOUNDS_DIR.glob("*.wav") if f.stem not in {e["id"] for e in S.load_catalog()}]
        check("le fichier refusé n'est pas conservé", not orphan, orphan)
        st, p = await call(bot._handle_upload_sound, 20, form=upload_form(G1_ID, "court", data=tiny_wav(1), filename="ok.wav"))
        check("son dans la limite de durée accepté", st == 200, (st, p))
    else:
        print("SKIP  durée (ffprobe absent)")
    settings_set(G1_ID, max_file_mb=0, max_duration_s=0)

    print("\n--- Modification (PATCH) ---")
    rn = lambda uid, sid, name="X": call(bot._handle_update_sound, uid, {"name": name}, {"id": sid})
    st, _ = await rn(20, sound_a, "renommé par l'auteur"); check("auteur renomme son son", st == 200, st)
    st, p = await rn(21, sound_a); check("autre membre -> 403 avec message", st == 403 and "auteur" in p["error"], (st, p))
    st, _ = await rn(10, sound_a, "par admin"); check("admin Discord renomme", st == 200, st)
    st, _ = await rn(1, sound_a, "par proprio"); check("propriétaire renomme", st == 200, st)
    st, _ = await rn(11, sound_a); check("membre avec rôle NON encore désigné -> 403", st == 403, st)
    settings_set(G1_ID, admin_role_id="555")
    st, _ = await rn(11, sound_a, "par rôle"); check("rôle désigné -> admin", st == 200, st)
    st, _ = await rn(30, sound_a); check("non-membre : son invisible -> 404", st == 404, st)
    st, _ = await rn(20, "legacy1"); check("ancien son : auteur inconnu -> membre 403", st == 403, st)
    st, _ = await rn(10, "legacy1", "ancien renommé"); check("ancien son : admin OK", st == 200, st)
    st, p = await call(bot._handle_update_sound, 20, {"emoji": "🔥"}, {"id": sound_a})
    check("auteur change l'emoji", st == 200 and p["emoji"] == "🔥", (st, p))
    st, _ = await call(bot._handle_update_sound, 21, {"emoji": "💀"}, {"id": sound_a}); check("autre membre : emoji refusé", st == 403, st)

    print("\n--- Partage inter-guildes + droits natifs ---")
    st, p = await call(bot._handle_upload_sound, 22, form=upload_form(G1_ID, "sonB22"))
    sound_b = p["id"]  # natif G1, auteur 22 (membre aussi de G2)
    st, _ = await call(bot._handle_shared_category_sound, 22, {"guild_id": G2_ID, "sound_id": sound_b, "action": "add"})
    check("auteur partage SON son vers une autre de ses guildes", st == 200, st)
    st, _ = await call(bot._handle_shared_category_sound, 22, {"guild_id": G2_ID, "sound_id": sound_a, "action": "add"})
    check("membre ne partage pas le son d'un autre -> 403", st == 403, st)
    st, _ = await call(bot._handle_shared_category_sound, 31, {"guild_id": G2_ID, "sound_id": sound_a, "action": "add"})
    check("admin de G2 mais non-membre de G1 : son invisible -> 404", st == 404, st)
    st, _ = await call(bot._handle_shared_category_sound, 22, {"guild_id": G1_ID, "sound_id": sound_a, "action": "add"})
    check("ajout vers la guilde native d'un son d'autrui -> 403", st == 403, st)
    st, _ = await call(bot._handle_shared_category_sound, 10, {"guild_id": G1_ID, "sound_id": sound_a, "action": "add"})
    check("admin de la guilde cible partage n'importe quel son visible", st == 200, st)
    st, _ = await call(bot._handle_shared_category_sound, 21, {"guild_id": G1_ID, "sound_id": sound_a, "action": "remove"})
    check("retrait par un membre ordinaire -> 403", st == 403, st)
    st, _ = await call(bot._handle_shared_category_sound, 10, {"guild_id": G1_ID, "sound_id": sound_a, "action": "remove"})
    check("retrait par l'admin", st == 200, st)
    st, _ = await call(bot._handle_shared_category_sound, 20, {"guild_id": G2_ID, "sound_id": sound_a, "action": "add"})
    check("non-membre de la guilde cible -> 403", st == 403, st)
    st, _ = await call(bot._handle_shared_category_sound, 20, {"guild_id": G1_ID, "sound_id": sound_a, "action": "bogus"})
    check("action invalide -> 400", st == 400, st)

    # Admin de G2 uniquement : voit sound_b (partagé dans G2) mais ne peut pas le modifier (guilde native = G1)
    st, _ = await rn(31, sound_b)
    check("admin de la guilde où le son est PARTAGÉ ne peut pas le renommer (droits = guilde native)", st == 403, st)

    print("\n--- Listing /sounds et /my-guilds ---")
    def flags(payload, sid): return next(((e["is_mine"], e["can_edit"]) for e in payload["sounds"] if e["id"] == sid), None)
    _, l20 = await call(bot._handle_list_sounds, 20); _, l21 = await call(bot._handle_list_sounds, 21)
    _, l10 = await call(bot._handle_list_sounds, 10); _, l22 = await call(bot._handle_list_sounds, 22)
    check("auteur : is_mine/can_edit", flags(l20, sound_a) == (True, True), flags(l20, sound_a))
    check("autre membre : ni mine ni éditable", flags(l21, sound_a) == (False, False), flags(l21, sound_a))
    check("admin : éditable sans être l'auteur", flags(l10, sound_a) == (False, True), flags(l10, sound_a))
    check("ancien son éditable par l'admin seulement", flags(l10, "legacy1") == (False, True) and flags(l20, "legacy1") == (False, False))
    check("membre de G1 et G2 voit son son natif G1 : éditable (membre de la guilde native)", flags(l22, sound_b) == (True, True))
    _, mg = await call(bot._handle_my_guilds, 10)
    check("/my-guilds : is_admin/can_upload", mg["guilds"][0]["is_admin"] and mg["guilds"][0]["can_upload"], mg)
    settings_set(G1_ID, upload_admins_only=True)
    _, mg20 = await call(bot._handle_my_guilds, 20)
    check("/my-guilds : can_upload faux quand l'upload est restreint", mg20["guilds"][0]["can_upload"] is False and not mg20["guilds"][0]["is_admin"], mg20)
    settings_set(G1_ID, upload_admins_only=False)

    print("\n--- Suppression = corbeille, restauration ---")
    st, _ = await call(bot._handle_delete_sound, 21, match={"id": sound_a}); check("autre membre ne supprime pas", st == 403, st)
    st, _ = await call(bot._handle_shared_category_sound, 10, {"guild_id": G1_ID, "sound_id": sound_a, "action": "add"})
    sound_a_file = S.SOUNDS_DIR / f"{sound_a}.wav"
    st, _ = await call(bot._handle_delete_sound, 20, match={"id": sound_a}); check("auteur supprime son son", st == 200, st)
    check("catalogue : son retiré", not any(e["id"] == sound_a for e in S.load_catalog()))
    check("fichier déplacé à la corbeille (pas effacé)", (S.TRASH_DIR / f"{sound_a}.wav").exists() and not sound_a_file.exists())
    check("retiré des catégories partagées", sound_a not in S.load_shared_categories().get(G1_ID, []))
    rec = next(t for t in S.load_trash() if t["id"] == sound_a)
    check("corbeille mémorise auteur/suppresseur/partages", rec["deleted_by"] == "20" and rec["uploaded_by"] == "20" and G1_ID in rec["shared_in"], rec)

    adm = lambda h, uid, gid, **kw: call(h, uid, match={"guild_id": gid, **kw.pop("match", {})}, **kw)
    st, p = await adm(bot._handle_admin_trash, 10, G1_ID)
    check("admin voit la corbeille de sa guilde", st == 200 and any(i["id"] == sound_a for i in p["trash"]), (st, p))
    st, _ = await adm(bot._handle_admin_trash, 21, G1_ID); check("membre : corbeille -> 403", st == 403, st)
    st, _ = await adm(bot._handle_admin_trash, 31, G1_ID); check("admin d'une AUTRE guilde : corbeille -> 403", st == 403, st)
    st, _ = await adm(bot._handle_admin_restore, 20, G1_ID, match={"sound_id": sound_a}); check("l'auteur ne restaure pas (admin seulement)", st == 403, st)
    st, _ = await adm(bot._handle_admin_restore, 31, G1_ID, match={"sound_id": sound_a}); check("admin d'une autre guilde ne restaure pas", st == 403, st)
    st, _ = await adm(bot._handle_admin_restore, 10, G1_ID, match={"sound_id": "inconnu"}); check("restauration d'un id inconnu -> 404", st == 404, st)
    st, _ = await adm(bot._handle_admin_restore, 10, G1_ID, match={"sound_id": sound_a}); check("admin restaure", st == 200, st)
    check("restauré : catalogue + fichier + partages", any(e["id"] == sound_a for e in S.load_catalog()) and sound_a_file.exists()
          and sound_a in S.load_shared_categories().get(G1_ID, []) and not (S.TRASH_DIR / f"{sound_a}.wav").exists())
    check("restauré : auteur conservé", next(e for e in S.load_catalog() if e["id"] == sound_a).get("uploaded_by") == "20")

    print("\n--- Purge de la corbeille ---")
    st, _ = await call(bot._handle_delete_sound, 10, match={"id": "legacy1"})
    S.TRASH_RETENTION_DAYS = 30
    check("pas de purge avant l'échéance", S.purge_expired_trash() == [])
    trash = S.load_trash(); trash[0]["deleted_at"] = time.time() - 31 * 86400; S.save_trash(trash)
    purged = S.purge_expired_trash()
    check("purge après 30 jours : enregistrement + fichier supprimés", [r["id"] for r in purged] == ["legacy1"]
          and not (S.TRASH_DIR / "legacy1.wav").exists() and S.load_trash() == [])
    S.TRASH_RETENTION_DAYS = 0
    check("rétention 0 = jamais de purge", S.purge_expired_trash() == [])

    print("\n--- Endpoints admin : réglages ---")
    st, _ = await adm(bot._handle_admin_get_settings, 21, G1_ID); check("membre : réglages -> 403", st == 403, st)
    st, p = await adm(bot._handle_admin_get_settings, 10, G1_ID)
    check("admin lit les réglages + rôles (sans @everyone ni rôles gérés)",
          st == 200 and [r["id"] for r in p["roles"]] == ["555", "556"], (st, p))
    st, p = await call(bot._handle_admin_put_settings, 10, {"upload_admins_only": True, "max_sounds": 50, "admin_role_id": "556"}, {"guild_id": G1_ID})
    check("admin modifie les réglages", st == 200 and p["settings"]["max_sounds"] == 50 and p["settings"]["admin_role_id"] == "556", (st, p))
    for body in ({"admin_role_id": "99999"}, {"max_sounds": -1}, {"max_sounds": True}, {"upload_admins_only": "oui"}, {"play_rate_per_min": 9999}):
        st, _ = await call(bot._handle_admin_put_settings, 10, body, {"guild_id": G1_ID})
        check(f"validation : {body} -> 400", st == 400, st)
    st, _ = await call(bot._handle_admin_put_settings, 21, {"max_sounds": 1}, {"guild_id": G1_ID}); check("membre ne peut pas modifier les réglages", st == 403, st)
    st, p = await call(bot._handle_admin_put_settings, 10, {"admin_role_id": None, "upload_admins_only": False, "max_sounds": 0}, {"guild_id": G1_ID})
    check("rôle admin retiré / réglages remis à zéro", st == 200 and p["settings"]["admin_role_id"] is None, (st, p))

    print("\n--- Endpoints admin : blocage ---")
    st, p = await call(bot._handle_admin_blocked, 10, {"user_id": "21", "blocked": True}, {"guild_id": G1_ID})
    check("admin bloque un membre", st == 200 and p["blocked"][0]["user_id"] == "21" and p["blocked"][0]["username"] == "user21", (st, p))
    settings_set(G1_ID, admin_role_id="555")
    st, _ = await call(bot._handle_admin_blocked, 10, {"user_id": "11", "blocked": True}, {"guild_id": G1_ID})
    check("impossible de bloquer un admin (rôle désigné)", st == 400, st)
    st, _ = await call(bot._handle_admin_blocked, 10, {"user_id": "10", "blocked": True}, {"guild_id": G1_ID})
    check("impossible de bloquer un admin Discord", st == 400, st)
    st, _ = await call(bot._handle_admin_blocked, 21, {"user_id": "20", "blocked": True}, {"guild_id": G1_ID}); check("membre ne bloque personne", st == 403, st)
    st, _ = await call(bot._handle_admin_blocked, 10, {"user_id": "abc", "blocked": True}, {"guild_id": G1_ID}); check("user_id invalide -> 400", st == 400, st)
    st, _ = await call(bot._handle_upload_sound, 21, form=upload_form(G1_ID)); check("le membre bloqué ne peut plus uploader", st == 403, st)
    st, p = await call(bot._handle_admin_blocked, 10, {"user_id": "21", "blocked": False}, {"guild_id": G1_ID})
    check("déblocage", st == 200 and p["blocked"] == [], (st, p))
    st, _ = await call(bot._handle_upload_sound, 21, form=upload_form(G1_ID, "retour")); check("débloqué : peut uploader", st == 200, st)
    settings_set(G1_ID, admin_role_id=None)

    print("\n--- Endpoints admin : sons, stats, journal ---")
    st, p = await adm(bot._handle_admin_sounds, 10, G1_ID)
    names = {s["name"]: s for s in p["sounds"]}
    check("liste des sons natifs avec auteur/date/taille", st == 200 and names["retour"]["uploaded_by_name"] == "user21"
          and names["retour"]["uploaded_at"] and names["retour"]["size"] and "sonB22" in names, (st, names.keys()))
    st, _ = await adm(bot._handle_admin_sounds, 21, G1_ID); check("membre : liste admin -> 403", st == 403, st)
    now = time.time()
    with open(S.STATS_PATH, "w", encoding="utf-8") as f:
        for ts, sid, uid in [(now, sound_a, "20"), (now, sound_a, "21"), (now - 86400, sound_b, "20"), (now - 40 * 86400, sound_b, "20")]:
            f.write(json.dumps({"ts": ts, "user_id": uid, "username": f"user{uid}", "avatar_url": None, "sound_id": sid,
                                "sound_name": "nom d'époque", "emoji": None, "guild_id": G1_ID, "guild_name": "g1"}) + "\n")
        f.write(json.dumps({"ts": now, "user_id": "20", "sound_id": sound_a, "guild_id": G2_ID}) + "\n")
        f.write("ligne corrompue\n")
    st, p = await call(bot._handle_admin_stats, 10, query={"days": "30"}, match={"guild_id": G1_ID})
    check("stats : 3 lectures sur 30 j (la plus vieille et celle de G2 exclues)", st == 200 and p["total_plays"] == 3, (st, p))
    check("stats : top son = sonA (nom actuel, pas celui d'époque)", p["top_sounds"][0]["sound_id"] == sound_a
          and p["top_sounds"][0]["count"] == 2 and p["top_sounds"][0]["name"] != "nom d'époque", p["top_sounds"])
    check("stats : top utilisateur", p["top_users"][0]["user_id"] == "20" and p["top_users"][0]["count"] == 2, p["top_users"])
    check("stats : 30 jours continus, total cohérent", len(p["per_day"]) == 30 and sum(d["count"] for d in p["per_day"]) == 3)
    st, _ = await call(bot._handle_admin_stats, 21, query={}, match={"guild_id": G1_ID}); check("membre : stats -> 403", st == 403, st)
    st, _ = await call(bot._handle_admin_stats, 10, query={"days": "x"}, match={"guild_id": G1_ID}); check("stats : days invalide -> 400", st == 400, st)
    st, p = await adm(bot._handle_admin_audit, 10, G1_ID)
    actions = [e["action"] for e in p["entries"]]
    check("journal : upload/rename/delete/restore/settings/block tous tracés",
          st == 200 and {"upload", "rename", "emoji", "delete", "restore", "share_add", "share_remove", "settings", "block", "unblock", "purge"} - set(actions) == {"purge"}, set(actions))
    check("journal : plus récent d'abord", [e["ts"] for e in p["entries"]] == sorted((e["ts"] for e in p["entries"]), reverse=True))
    st, p2 = await call(bot._handle_admin_audit, 10, query={"limit": "2"}, match={"guild_id": G1_ID})
    check("journal : limit respecté", len(p2["entries"]) == 2)
    st, _ = await adm(bot._handle_admin_audit, 21, G1_ID); check("membre : journal -> 403", st == 403, st)
    check("journal d'une guilde ne contient pas celles des autres", all(e["guild_id"] == G1_ID for e in p["entries"]))

    print("\n--- Anti-spam à la lecture ---")
    S.save_catalog(S.load_catalog())  # idem
    play_sound = next(e["id"] for e in S.load_catalog() if e["guild_id"] == G1_ID)
    (S.SOUNDS_DIR / f"{play_sound}.wav").write_bytes(b"x") if not (S.SOUNDS_DIR / f"{play_sound}.wav").exists() else None

    class VC:
        def __init__(self, ids):
            self.channel = type("C", (), {"members": [Member(i) for i in ids], "name": "salon"})()
        def is_connected(self): return True
    class Mixer:
        def __init__(self): self.n = 0
        def add(self, *a, **k): self.n += 1
    bot.voice_clients_map = {G1.id: VC([10, 20, 21])}
    bot.mixers = {G1.id: Mixer()}
    settings_set(G1_ID, play_rate_per_min=2)
    codes = [(await call(bot._handle_play, 21, {"id": play_sound}))[0] for _ in range(4)]
    check("anti-spam : 2 lectures passent puis 429", codes == [200, 200, 429, 429], codes)
    st, p = await call(bot._handle_play, 21, {"id": play_sound})
    check("429 : message + retry_after", st == 429 and 1 <= p["retry_after"] <= 60 and "réessayez" in p["error"], (st, p))
    check("lectures refusées non envoyées au mixeur", bot.mixers[G1.id].n == 2, bot.mixers[G1.id].n)
    codes = [(await call(bot._handle_play, 10, {"id": play_sound}))[0] for _ in range(5)]
    check("admin exempté de l'anti-spam", codes == [200] * 5, codes)
    codes = [(await call(bot._handle_play, 20, {"id": play_sound}))[0] for _ in range(3)]
    check("fenêtre indépendante par utilisateur", codes == [200, 200, 429], codes)
    bot._play_history[(G1.id, "21")].clear()
    for t in range(2): bot._play_history[(G1.id, "21")].append(time.monotonic() - 61)
    st, _ = await call(bot._handle_play, 21, {"id": play_sound})
    check("la fenêtre glisse : lectures vieilles de 61 s oubliées", st == 200, st)
    settings_set(G1_ID, play_rate_per_min=0)
    codes = [(await call(bot._handle_play, 21, {"id": play_sound}))[0] for _ in range(6)]
    check("limite 0 = désactivé", codes == [200] * 6, codes)

    print("\n--- Découpe non destructive + emoji à l'ajout ---")
    full = tiny_wav(5)
    def tform(name, start=None, end=None, emoji=None):
        f = upload_form(G1_ID, name, data=full, filename="t.wav")
        if start is not None: f["trim_start_ms"] = str(start)
        if end is not None: f["trim_end_ms"] = str(end)
        if emoji is not None: f["emoji"] = emoji
        return f
    st, p = await call(bot._handle_upload_sound, 20, form=tform("coupé", 1000, 3500, "🔥"))
    check("upload avec découpe + emoji : champs enregistrés", st == 200 and p["trim_start_ms"] == 1000 and p["trim_end_ms"] == 3500 and p["emoji"] == "🔥", (st, p))
    trimmed_id = p["id"]
    stored = (S.SOUNDS_DIR / f"{trimmed_id}.wav").read_bytes()
    check("le fichier stocké est l'original COMPLET (rien de perdu)", stored == full, len(stored))
    check("hash = hash de l'original", p["hash"] == __import__("hashlib").sha256(full).hexdigest())
    st, p = await call(bot._handle_upload_sound, 20, form=tform("entier"))
    check("upload sans découpe : pas de champs de découpe", st == 200 and "trim_start_ms" not in p and "emoji" not in p, (st, p))
    plain_id = p["id"]
    for label, a, b in [("seulement le début", 1000, None), ("seulement la fin", None, 3000), ("fin <= début", 3000, 3000),
                        ("moins de 0,1 s", 1000, 1050), ("début négatif", -5, 3000), ("hors garde-fou", 0, 99999999)]:
        st, _ = await call(bot._handle_upload_sound, 20, form=tform("x", a, b))
        check(f"découpe invalide ({label}) -> 400", st == 400, st)
    f = tform("x", 1000, 3000); f["trim_start_ms"] = "abc"
    st, _ = await call(bot._handle_upload_sound, 20, form=f); check("découpe non numérique -> 400", st == 400, st)

    if shutil.which("ffprobe"):
        settings_set(G1_ID, max_duration_s=2)
        st, _ = await call(bot._handle_upload_sound, 20, form=tform("court-coupé", 1000, 2500))
        check("quota de durée sur la portion gardée (1,5 s <= 2 s) : accepté", st == 200, st)
        st, p = await call(bot._handle_upload_sound, 20, form=tform("long-coupé", 0, 3500))
        check("quota de durée sur la portion gardée (3,5 s > 2 s) : refusé", st == 413, (st, p))
        st, _ = await call(bot._handle_upload_sound, 20, form=tform("entier-trop-long"))
        check("sans découpe, le quota mesure le fichier entier (5 s > 2 s) : refusé", st == 413, st)
        settings_set(G1_ID, max_duration_s=0)

    print("\n--- Redécouper (PATCH) ---")
    pt = lambda uid, sid, body: call(bot._handle_update_sound, uid, body, {"id": sid})
    st, p = await pt(20, plain_id, {"trim_start_ms": 500, "trim_end_ms": 2000})
    check("l'auteur découpe un son entier", st == 200 and p["trim_start_ms"] == 500 and p["trim_end_ms"] == 2000, (st, p))
    st, p = await pt(10, plain_id, {"trim_start_ms": 700, "trim_end_ms": 2200, "name": "renommé+recoupé", "emoji": "💥"})
    check("un admin modifie nom + emoji + découpe d'un coup", st == 200 and p["name"] == "renommé+recoupé" and p["emoji"] == "💥" and p["trim_start_ms"] == 700, (st, p))
    st, _ = await pt(21, plain_id, {"trim_start_ms": 0, "trim_end_ms": 1000}); check("un autre membre ne peut pas recouper -> 403", st == 403, st)
    st, _ = await pt(20, plain_id, {"trim_start_ms": 500}); check("une seule borne -> 400", st == 400, st)
    before = next(e for e in S.load_catalog() if e["id"] == plain_id)
    st, _ = await pt(20, plain_id, {"name": "ne doit pas passer", "trim_start_ms": 5, "trim_end_ms": 6})
    after = next(e for e in S.load_catalog() if e["id"] == plain_id)
    check("refus = aucune modification partielle (nom inchangé)", st == 400 and after["name"] == before["name"], (st, after["name"]))
    st, p = await pt(20, plain_id, {"trim_start_ms": None, "trim_end_ms": None})
    check("les deux nuls : la découpe est retirée, son entier retrouvé", st == 200 and "trim_start_ms" not in p and "trim_end_ms" not in p, (st, p))
    check("le fichier complet est toujours là après les recoupes", (S.SOUNDS_DIR / f"{plain_id}.wav").read_bytes() == full)
    st, p = await call(bot._handle_admin_audit, 10, query={"limit": "500"}, match={"guild_id": G1_ID})
    trims = [e for e in p["entries"] if e["action"] == "trim"]
    check("journal : les (re)découpes sont tracées", len(trims) >= 3, trims[:2])

    print("\n--- Lecture : le serveur applique la découpe ---")
    bot.voice_clients_map = {G1.id: VC([20])}
    bot.mixers = {G1.id: Mixer()}
    FF_CALLS.clear()
    st, _ = await call(bot._handle_play, 20, {"id": trimmed_id})
    check("son découpé : ffmpeg reçoit -ss 1.000 et -t 2.500", st == 200 and FF_CALLS[-1][1] == {"before_options": "-ss 1.000", "options": "-t 2.500"}, FF_CALLS[-1:])
    st, _ = await call(bot._handle_play, 20, {"id": plain_id})
    check("son entier : aucune option de découpe", st == 200 and FF_CALLS[-1][1] == {"before_options": None, "options": None}, FF_CALLS[-1:])
    S.save_catalog(S.load_catalog() + [{"id": "oldtrim", "name": "ancien", "extension": ".wav", "hash": "h", "guild_id": G1_ID, "trim_start_ms": "oops", "trim_end_ms": 5}])
    (S.SOUNDS_DIR / "oldtrim.wav").write_bytes(b"x")
    st, _ = await call(bot._handle_play, 20, {"id": "oldtrim"})
    check("champs de découpe corrompus : ignorés, lecture entière", st == 200 and FF_CALLS[-1][1] == {"before_options": None, "options": None}, FF_CALLS[-1:])

    print("\n--- Écritures atomiques ---")
    check("aucun fichier .tmp résiduel", not list(S.DATA_DIR.rglob("*.tmp")), list(S.DATA_DIR.rglob("*.tmp")))


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir()
for leftover in (S.GUILD_SETTINGS_PATH, S.AUDIT_PATH, S.STATS_PATH, S.SHARED_CATEGORIES_PATH):
    leftover.unlink(missing_ok=True)
asyncio.run(main())
print("\nRÉSULTAT :", "TOUT PASSE" if all(results) else f"{results.count(False)} échec(s) sur {len(results)}", f"({len(results)} vérifications)")
