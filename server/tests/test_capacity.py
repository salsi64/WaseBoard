"""Jalon 8 : plafonds de ressources, quotas/plafonds de guilde, limitation de débit, /instance.
Vrai code de server.py / wb_config.py, faux objets Discord. Lancé via tests/run_all.sh (dossier de données isolé et jetable, voir _env.py)."""
import asyncio
import io
import json
import shutil
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import aiohttp  # noqa: E402
import discord  # noqa: E402
import server as S  # noqa: E402
import wb_config  # noqa: E402
import diagnostics as D  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


# ---------- faux Discord (mêmes principes que test_roles.py) ----------
class Perms:
    def __init__(self, administrator=False): self.administrator = administrator

class Member:
    def __init__(self, uid, administrator=False):
        self.id, self.guild_permissions, self.roles, self.display_name = uid, Perms(administrator), [], f"user{uid}"

class Guild:
    def __init__(self, gid, owner_id, name=None):
        self.id, self.owner_id, self.roles, self.name, self.icon = gid, owner_id, [], name or f"guild{gid}", None
        self.members = {}
    def get_member(self, uid): return self.members.get(uid)
    def get_role(self, rid): return None

G1 = Guild(2001, owner_id=1, name="Alpha")
G2 = Guild(2002, owner_id=2, name="Beta")
G3 = Guild(2003, owner_id=3, name="Gamma")
for g, members in ((G1, [(1, False), (10, True), (20, False), (21, False)]), (G2, [(2, False), (20, False)]), (G3, [(3, False), (20, False)])):
    for uid, adm in members:
        g.members[uid] = Member(uid, adm)
GUILDS = [G1, G2, G3]
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

for uid in (1, 10, 20, 21, 2, 3):
    bot.oauth_sessions[f"tok{uid}"] = {
        "user_id": str(uid), "username": f"user{uid}", "avatar_url": None,
        "discord_access_token": "x", "discord_refresh_token": "y",
        "discord_token_expires_at": time.time() + 10**6, "created_at": 0, "last_used_at": 0,
    }


class Upload:
    def __init__(self, filename, data): self.filename, self.file = filename, io.BytesIO(data)

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
    return resp.status, payload, resp


def upload_form(guild, name="son", data=b"x", filename="a.wav"):
    return {"name": name, "guild_id": str(guild.id), "file": Upload(filename, data)}


class Limits:
    """Pose des valeurs de configuration le temps d'un bloc, puis restaure ce qu'il y avait."""
    def __init__(self, **values): self.values, self.saved = values, {}
    def __enter__(self):
        for k, v in self.values.items(): self.saved[k] = getattr(S, k); setattr(S, k, v)
    def __exit__(self, *a):
        for k, v in self.saved.items(): setattr(S, k, v)


S.SHARED_SECRET = "s" * 40
S.GUILD_ID = None


async def main():
    print("--- wb_config : nouvelles clés ---")
    c = wb_config.load_config({"WASEBOARD_MAX_GUILDS": "50", "WASEBOARD_MAX_FFMPEG_PROCESSES": "40",
                               "WASEBOARD_DEFAULT_GUILD_LIMITS": '{"max_total_mb": 500, "max_sounds": 200}',
                               "WASEBOARD_GUILD_LIMIT_CEILINGS": '{"max_file_mb": 25}'}, Path("/nonexistent.json"))
    check("plafonds et objets JSON lus depuis l'environnement", c["max_guilds"] == 50 and c["max_ffmpeg_processes"] == 40
          and c["default_guild_limits"] == {"max_total_mb": 500, "max_sounds": 200} and c["guild_limit_ceilings"] == {"max_file_mb": 25}, c)
    for label, env in (("plafond négatif", {"WASEBOARD_MAX_GUILDS": "-1"}),
                       ("réglage de guilde inconnu", {"WASEBOARD_DEFAULT_GUILD_LIMITS": '{"foo": 1}'}),
                       ("valeur non numérique", {"WASEBOARD_GUILD_LIMIT_CEILINGS": '{"max_sounds": "x"}'}),
                       ("JSON qui n'est pas un objet", {"WASEBOARD_GUILD_LIMIT_CEILINGS": "[1]"}),
                       ("JSON invalide", {"WASEBOARD_DEFAULT_GUILD_LIMITS": "{pas du json"})):
        try:
            wb_config.load_config(env, Path("/nonexistent.json")); ok = False
        except wb_config.ConfigError:
            ok = True
        check(f"{label} -> ConfigError au démarrage", ok)
    try:
        cfg = Path("/tmp/wbcap_cfg.json"); cfg.write_text(json.dumps({"max_guilds": True}))
        wb_config.load_config({}, cfg); ok = False
    except wb_config.ConfigError:
        ok = True
    finally:
        Path("/tmp/wbcap_cfg.json").unlink(missing_ok=True)
    check("un booléen n'est pas un nombre valide (max_guilds: true)", ok)
    check("sans configuration : aucun plafond actif", S.MAX_GUILDS == 0 and S.MAX_CONCURRENT_VOICE == 0
          and S.MAX_FFMPEG_PROCESSES == 0 and S.HTTP_RATE_LIMIT_PER_MIN == 0 and S.DEFAULT_GUILD_LIMITS == {} and S.GUILD_LIMIT_CEILINGS == {})

    print("\n--- Réglages de guilde : défauts de l'hébergeur et plafonds ---")
    check("sans configuration hébergeur : réglages inchangés (rien de limité)", S.get_guild_settings("9999") == S.DEFAULT_GUILD_SETTINGS and S.DEFAULT_GUILD_SETTINGS["max_total_mb"] == 0)
    with Limits(DEFAULT_GUILD_LIMITS={"max_total_mb": 100, "max_sounds": 50}):
        g = S.get_guild_settings("7001")
        check("guilde sans réglages enregistrés : reçoit les valeurs par défaut de l'hébergeur", g["max_total_mb"] == 100 and g["max_sounds"] == 50, g)
        S.save_guild_settings("7002", {**S.DEFAULT_GUILD_SETTINGS, "max_total_mb": 0, "max_sounds": 0})
        g = S.get_guild_settings("7002")
        check("guilde qui a déjà enregistré ses réglages : INCHANGÉE (reste illimitée)", g["max_total_mb"] == 0 and g["max_sounds"] == 0, g)
        S.save_guild_settings("7003", {"upload_admins_only": True})  # ancien format, sans les nouvelles clés
        g = S.get_guild_settings("7003")
        check("ancien enregistrement partiel : pas de défauts imposés rétroactivement", g["max_total_mb"] == 0 and g["max_sounds"] == 0 and g["upload_admins_only"], g)
    with Limits(GUILD_LIMIT_CEILINGS={"max_file_mb": 20, "max_total_mb": 500}):
        g = S.get_guild_settings("7001")
        check("plafond : « illimité » (0) devient le plafond", g["max_file_mb"] == 20 and g["max_total_mb"] == 500, g)
        S.save_guild_settings("7004", {**S.DEFAULT_GUILD_SETTINGS, "max_file_mb": 50, "max_total_mb": 100})
        g = S.get_guild_settings("7004")
        check("plafond : valeur supérieure ramenée, valeur inférieure conservée", g["max_file_mb"] == 20 and g["max_total_mb"] == 100, g)
        raw = S.get_guild_settings("7004", clamp=False)
        check("clamp=False : valeurs brutes enregistrées", raw["max_file_mb"] == 50)
        check("réglages sans plafond configuré (max_sounds) : intacts", S.get_guild_settings("7001")["max_sounds"] == 0)
    with Limits(GUILD_LIMIT_CEILINGS={"max_sounds": 0}):
        check("plafond à 0 = pas de plafond", S.get_guild_settings("7001")["max_sounds"] == 0)

    print("\n--- Panneau admin : plafonds, usage, écriture des valeurs brutes ---")
    G1_ID = str(G1.id)
    S.save_catalog([])
    async def adm(handler, uid, body=None, match=None):
        return await call(handler, uid, body, match or {"guild_id": G1_ID})
    with Limits(GUILD_LIMIT_CEILINGS={"max_file_mb": 20, "max_total_mb": 500}):
        st, p, _ = await adm(bot._handle_admin_get_settings, 10)
        check("GET : valeurs effectives (plafonnées), plafonds et usage fournis",
              st == 200 and p["settings"]["max_file_mb"] == 20 and p["ceilings"] == {"max_file_mb": 20, "max_total_mb": 500}
              and p["usage"] == {"sounds": 0, "total_mb": 0.0}, (st, p))
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_file_mb": 50})
        check("PUT au-dessus du plafond -> 400 qui nomme le plafond", st == 400 and "limité à 20" in p["error"], (st, p))
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_file_mb": 0})
        check("PUT « illimité » sur un réglage plafonné -> 400", st == 400 and "illimité" in p["error"], (st, p))
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_total_mb": 1000})
        check("PUT max_total_mb au-dessus du plafond -> 400", st == 400, (st, p))
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_file_mb": 15, "max_total_mb": 200, "max_sounds": 30})
        check("PUT dans les limites -> 200 et valeurs prises en compte", st == 200 and p["settings"]["max_file_mb"] == 15 and p["settings"]["max_total_mb"] == 200 and p["settings"]["max_sounds"] == 30, (st, p))
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_sounds": 31})
        saved = S.load_all_guild_settings()[G1_ID]
        check("l'enregistrement conserve les valeurs brutes de l'admin (pas le plafond)", saved["max_file_mb"] == 15 and saved["max_total_mb"] == 200, saved)
        # guilde jamais enregistrée (G2, admin = son propriétaire) : le plafond ne doit pas être écrit comme si l'admin l'avait choisi
        st, p, _ = await call(bot._handle_admin_put_settings, 2, {"max_sounds": 5}, {"guild_id": str(G2.id)})
        raw2 = S.load_all_guild_settings()[str(G2.id)]
        check("guilde jamais enregistrée : seule la valeur modifiée est choisie, le plafond n'est pas écrit (max_file_mb reste 0)",
              st == 200 and raw2["max_sounds"] == 5 and raw2["max_file_mb"] == 0 and raw2["max_total_mb"] == 0, (st, raw2))
        check("…mais la réponse affiche bien les valeurs effectives (plafonnées)", p["settings"]["max_file_mb"] == 20 and p["settings"]["max_total_mb"] == 500, p["settings"])
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"play_rate_per_min": 0})
        check("réglage sans plafond (anti-spam) : toujours modifiable librement, 0 permis", st == 200)
        st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_total_mb": 2_000_000})
        check("borne de validation propre au réglage conservée (max_total_mb <= 1 048 576)", st == 400)
    st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_file_mb": 900, "max_total_mb": 5000})
    check("sans plafonds configurés : valeurs libres comme avant", st == 200 and p["settings"]["max_total_mb"] == 5000 and p["ceilings"] == {}, (st, p))
    st, p, _ = await adm(bot._handle_admin_put_settings, 10, {"max_sources_per_guild": 5})
    check("max_sources_per_guild modifiable via le panel admin, comme ses réglages voisins", st == 200 and p["settings"]["max_sources_per_guild"] == 5, (st, p))
    settings_before = S.get_guild_settings(G1.id)
    S.save_guild_settings(G1.id, {**settings_before, "max_file_mb": 0, "max_total_mb": 0, "max_sounds": 0, "play_rate_per_min": 0, "max_sources_per_guild": 0})

    print("\n--- Quota d'espace disque cumulé (max_total_mb) ---")
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS, "max_total_mb": 1})
    big = b"x" * 600_000
    st, p, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G1, "un", big))
    check("1er fichier de 600 Ko accepté (quota 1 Mo)", st == 200, (st, p))
    st, p, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G1, "deux", big))
    check("2e fichier de 600 Ko refusé (413) avec le détail de l'espace", st == 413 and "Espace insuffisant" in p["error"] and "1 Mo" in p["error"], (st, p))
    check("le fichier refusé n'a laissé aucune trace sur le disque", len([f for f in S.SOUNDS_DIR.iterdir() if f.suffix == ".wav"]) == 1)
    st, p, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G1, "petit", b"y" * 10_000))
    check("un petit fichier qui tient encore dans le reste passe", st == 200, (st, p))
    st, p, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G2, "autre guilde", big))
    check("une autre guilde n'est pas affectée par le quota de la première", st == 200, (st, p))
    st, p, _ = await adm(bot._handle_admin_get_settings, 10)
    check("usage affiché : 2 sons, ~0,6 Mo", p["usage"]["sounds"] == 2 and 0.5 <= p["usage"]["total_mb"] <= 0.7, p["usage"])
    with Limits(GUILD_LIMIT_CEILINGS={"max_total_mb": 1}):
        S.save_guild_settings(G3.id, {**S.DEFAULT_GUILD_SETTINGS})
        st, p, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G3, "g3a", big))
        st2, p2, _ = await call(bot._handle_upload_sound, 20, form=upload_form(G3, "g3b", big))
        check("plafond de l'hébergeur appliqué à l'upload même si la guilde est « illimitée »", st == 200 and st2 == 413, (st, st2))
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS})

    print("\n--- Plafonds de lecture (sons par serveur, processus ffmpeg total) ---")
    cat = [{"id": f"s{i}", "name": f"son{i}", "extension": ".wav", "hash": f"h{i}", "guild_id": G1_ID} for i in range(1, 7)]
    cat += [{"id": f"t{i}", "name": f"tson{i}", "extension": ".wav", "hash": f"th{i}", "guild_id": str(G2.id)} for i in range(1, 4)]
    S.save_catalog(cat)
    for e in cat:
        (S.SOUNDS_DIR / f"{e['id']}{e['extension']}").write_bytes(b"x")

    class VC:
        def __init__(self, ids, channel_name="salon"):
            self.channel = type("C", (), {"members": [Member(i) for i in ids], "name": channel_name})()
            self.moves = []
        def is_connected(self): return True
        async def move_to(self, channel): self.moves.append(channel)

    def fresh_mixers():
        bot.voice_clients_map = {G1.id: VC([10, 20, 21]), G2.id: VC([2, 20])}
        bot.mixers = {G1.id: S.MixingAudioSource(), G2.id: S.MixingAudioSource()}

    fresh_mixers()
    m = bot.mixers[G1.id]
    check("mixeur : count()/has() suivent les sons en lecture", m.count() == 0 and not m.has("a"))
    m.add("a", ("src", 1)); m.add("b", ("src", 2)); m.add("a", ("src", 3))
    check("rejouer la même clé remplace (count reste 2)", m.count() == 2 and m.has("a") and m.has("b"))
    m.clear()
    check("clear() remet le compteur à 0", m.count() == 0)

    async def play(uid, sid, gid=None):
        body = {"id": sid}
        if gid: body["guild_id"] = str(gid)
        return await call(bot._handle_play, uid, body)

    fresh_mixers()
    for n in range(1, 7):
        st, _, _ = await play(21, f"s{n}", G1.id)
        assert st == 200
    check("sans plafond : 6 sons simultanés, tous acceptés", bot.mixers[G1.id].count() == 6)

    fresh_mixers()
    # max_sources_per_guild est un réglage PAR SERVEUR (pas un plafond d'instance global, voir migration) :
    # posé directement dans les réglages enregistrés de G1, pas via Limits().
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS, "play_rate_per_min": 3, "max_sources_per_guild": 2})
    r1 = (await play(21, "s1", G1.id))[0]
    r2 = (await play(21, "s2", G1.id))[0]
    st, p, resp = await play(21, "s3", G1.id)
    check("max_sources_per_guild=2 (réglage de guilde) : 3e son différent refusé en 429 avec message et Retry-After",
          (r1, r2) == (200, 200) and st == 429 and "maximum 2" in p["error"] and p["retry_after"] == 2 and resp.headers.get("Retry-After") == "2", (r1, r2, st, p))
    check("le son refusé n'est pas parti dans le mixeur", bot.mixers[G1.id].count() == 2 and not bot.mixers[G1.id].has("s3"))
    r4 = (await play(21, "s1", G1.id))[0]
    check("rejouer un son déjà en lecture reste permis (il remplace)", r4 == 200 and bot.mixers[G1.id].count() == 2, r4)
    st, p, _ = await play(21, "s1", G1.id)
    check("le refus de capacité n'a PAS consommé l'anti-spam (3 lectures valides = limite atteinte ensuite)", st == 429 and "Trop de sons envoyés" in p["error"], (st, p))
    st, _, _ = await play(20, "t1", G2.id)
    check("une AUTRE guilde (sans ce réglage) n'est pas affectée : bien par serveur, pas global", st == 200, st)
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS})

    fresh_mixers()
    with Limits(MAX_FFMPEG_PROCESSES=3):
        codes = [(await play(21, "s1", G1.id))[0], (await play(21, "s2", G1.id))[0]]
        codes.append((await play(20, "t1", G2.id))[0])
        st, p, _ = await play(20, "t2", G2.id)
        check("max_ffmpeg_processes=3 : le 4e son (toutes guildes) est refusé", codes == [200, 200, 200] and st == 429 and "très sollicité" in p["error"], (codes, st, p))
        bot.mixers[G1.id].clear()
        st, _, _ = await play(20, "t2", G2.id)
        check("dès qu'un son se termine / est coupé, la capacité revient", st == 200)
        st, _, _ = await play(20, "t1", G2.id)
        check("rejouer un son en cours reste permis même à la limite", st == 200)

    print("\n--- Plafond de salons vocaux simultanés ---")
    fresh_mixers()
    chan = SimpleNamespace(name="vocal", guild=SimpleNamespace(name="Beta"))
    with Limits(MAX_CONCURRENT_VOICE=2):
        bot.voice_clients_map = {G1.id: VC([10]), G2.id: VC([2])}
        try:
            await bot.join_guild_voice(G3.id, chan); raised = None
        except S.CapacityError as ex:
            raised = str(ex)
        check("3e salon vocal refusé (CapacityError avec message utile)", raised is not None and "saturé" in raised and "2 salons" in raised, raised)
        vc = bot.voice_clients_map[G1.id]
        res = await bot.join_guild_voice(G1.id, chan)
        check("déplacer le bot dans une guilde déjà connectée reste permis", res is vc and vc.moves == [chan])
        async def fake_find(uid): return (G3.id, chan)
        bot.find_current_voice_channel = fake_find
        st, p, resp = await call(bot._handle_join_my_channel, 20)
        check("API join-my-channel : 503 + message + Retry-After", st == 503 and "saturé" in p["error"] and resp.headers.get("Retry-After") == "60", (st, p))

        class FakeResponse:
            def __init__(self): self.sent = []
            async def send_message(self, content=None, **kw): self.sent.append((content, kw))
        class FakeMember(discord.Member):
            voice = SimpleNamespace(channel=chan)
            def __init__(self): pass
        inter = SimpleNamespace(user=FakeMember(), guild_id=G3.id, response=FakeResponse())
        await S.join.callback(inter)
        text, kw = inter.response.sent[0]
        check("commande /join : message ⚠️ éphémère au lieu d'un plantage", "⚠️" in text and "saturé" in text and kw.get("ephemeral") is True, text)
    with Limits(MAX_CONCURRENT_VOICE=0):
        class VC2(VC):
            pass
        bot.voice_clients_map = {G1.id: VC([10]), G2.id: VC([2])}
        connected = []
        async def fake_connect(self_channel):
            connected.append(1); raise RuntimeError("connexion factice")
        chan2 = SimpleNamespace(connect=lambda: fake_connect(None), name="v", guild=None)
        try:
            await bot.join_guild_voice(G3.id, chan2)
        except RuntimeError:
            pass
        check("sans plafond : on tente bien de se connecter (aucun refus de capacité)", connected == [1])

    print("\n--- Plafond de serveurs Discord (on_guild_join) ---")
    class FakeOwner:
        def __init__(self, fail=False): self.dms, self.fail = [], fail
        async def send(self, text):
            if self.fail: raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "messages privés fermés")
            self.dms.append(text)
    class JoinGuild:
        def __init__(self, gid, owner): self.id, self.name, self.owner, self.owner_id, self.left = gid, f"Nouveau{gid}", owner, 1, False
        async def leave(self): self.left = True
    fake_list = [G1, G2, G3]
    S.WaseBoardServer.guilds = property(lambda self: fake_list)
    try:
        with Limits(MAX_GUILDS=3):
            owner = FakeOwner(); newg = JoinGuild(9001, owner)
            fake_list = [G1, G2, G3, newg]
            await bot.on_guild_join(newg)
            check("4e serveur avec max_guilds=3 : message au propriétaire puis le bot quitte", newg.left and len(owner.dms) == 1 and "capacité maximale" in owner.dms[0] and "Nouveau9001" in owner.dms[0], (newg.left, owner.dms))
            owner2 = FakeOwner(fail=True); g2 = JoinGuild(9002, owner2)
            await bot.on_guild_join(g2)
            check("messages privés fermés : le bot quitte quand même", g2.left)
            fake_list = [G1, G2, newg]
            owner3 = FakeOwner(); g3 = JoinGuild(9003, owner3); fake_list = [G1, G2, g3]
            await bot.on_guild_join(g3)
            check("sous le plafond (3/3 atteint exactement) : le serveur est accepté", not g3.left)
            check("serveur accepté : message de BIENVENUE au propriétaire (distinct du refus), mentionne /panneau",
                  len(owner3.dms) == 1 and "Nouveau9003" in owner3.dms[0] and "/panneau" in owner3.dms[0]
                  and "capacité maximale" not in owner3.dms[0], owner3.dms)
            check("le DM de bienvenue n'embarque pas de lien de connexion direct (expirerait avant lecture)",
                  "/connect/" not in owner3.dms[0], owner3.dms)
        with Limits(MAX_GUILDS=0):
            owner4 = FakeOwner(); g4 = JoinGuild(9004, owner4)
            fake_list = [G1, G2, G3, g4]
            await bot.on_guild_join(g4)
            check("sans plafond : jamais refusé", not g4.left)
            check("et reçoit quand même le message de bienvenue", len(owner4.dms) == 1 and "Nouveau9004" in owner4.dms[0])
        owner5 = FakeOwner(fail=True); g5 = JoinGuild(9005, owner5)
        fake_list = [G1, G2, G3, g5]
        await bot.on_guild_join(g5)
        check("messages privés fermés sur le DM de bienvenue : n'empêche pas le reste (pas d'exception)", not g5.left)
        check("les serveurs déjà présents ne sont jamais touchés (on_guild_join ne concerne que le nouveau)", all(not hasattr(g, "left") for g in (G1, G2, G3)))
    finally:
        del S.WaseBoardServer.guilds

    print("\n--- Limitation de débit : outils ---")
    rl = S.RateLimiter(3)
    waits = [rl.check(("upload", "1.1.1.1")) for _ in range(5)]
    check("3 passent, les suivantes attendent", waits[:3] == [None, None, None] and all(isinstance(w, int) and 1 <= w <= 60 for w in waits[3:]), waits)
    check("clés indépendantes (autre IP, autre route)", rl.check(("upload", "2.2.2.2")) is None and rl.check(("oauth", "1.1.1.1")) is None)
    check("limite 0 = désactivé", all(S.RateLimiter(0).check(("x", "y")) is None for _ in range(100)))
    real_monotonic = time.monotonic
    clock = [real_monotonic()]
    S.time.monotonic = lambda: clock[0]
    try:
        rl2 = S.RateLimiter(2)
        rl2.check(("a", "b")); rl2.check(("a", "b"))
        blocked = rl2.check(("a", "b")); clock[0] += 61
        check("la fenêtre glisse : 61 s plus tard, de nouveau permis", blocked is not None and rl2.check(("a", "b")) is None)
        rl3 = S.RateLimiter(1)
        for n in range(10005): rl3.check(("k", str(n)))
        clock[0] += 120
        rl3.check(("k", "nouveau"))
        check("garde-fou mémoire : les clés périmées sont oubliées", len(rl3._hits) < 100, len(rl3._hits))
    finally:
        S.time.monotonic = real_monotonic

    def fake_req(remote, xff=None, method="GET", path="/"):
        headers = {"X-Forwarded-For": xff} if xff else {}
        return SimpleNamespace(remote=remote, headers=headers, method=method, path=path)
    check("client_ip : pair public -> son adresse (X-Forwarded-For ignoré, forgeable)", S.client_ip(fake_req("8.8.8.8", "1.2.3.4")) == "8.8.8.8")
    check("client_ip : proxy local -> dernière entrée de X-Forwarded-For", S.client_ip(fake_req("127.0.0.1", "9.9.9.9")) == "9.9.9.9")
    check("client_ip : entrées forgées avant celle du proxy ignorées", S.client_ip(fake_req("127.0.0.1", "6.6.6.6, 7.7.7.7, 5.5.5.5")) == "5.5.5.5")
    check("client_ip : proxy Docker (réseau privé) pris en compte", S.client_ip(fake_req("172.18.0.2", "203.0.113.9")) == "203.0.113.9")
    check("client_ip : X-Forwarded-For invalide -> adresse du pair", S.client_ip(fake_req("127.0.0.1", "pas-une-ip")) == "127.0.0.1")
    check("client_ip : sans X-Forwarded-For -> pair", S.client_ip(fake_req("127.0.0.1")) == "127.0.0.1")
    check("client_ip : IPv6 supportée", S.client_ip(fake_req("::1", "2001:db8::1")) == "2001:db8::1")
    buckets = {("GET", "/connect/abc"): "connect", ("GET", "/oauth/client-id"): "oauth", ("POST", "/oauth/exchange"): "oauth",
               ("POST", "/sounds"): "upload", ("GET", "/sounds"): None, ("GET", "/activity"): None, ("GET", "/status"): None,
               ("POST", "/play"): None, ("GET", "/health"): None, ("POST", "/shared-categories/sounds"): None, ("PATCH", "/sounds/abc"): None}
    wrong = {k: S.rate_limit_bucket(fake_req("1.1.1.1", method=k[0], path=k[1])) for k in buckets if S.rate_limit_bucket(fake_req("1.1.1.1", method=k[0], path=k[1])) != buckets[k]}
    check("seules les routes publiques/coûteuses sont limitées (jamais /activity, /status, /play, /health)", not wrong, wrong)

    print("\n--- Limitation de débit : vrai serveur HTTP ---")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    S.HTTP_HOST, S.HTTP_PORT = "127.0.0.1", port
    S.OAUTH2_CLIENT_ID = "123"
    with Limits(RATE_LIMITER=S.RateLimiter(3)):
        await bot._start_http_server()
        headers = {"X-WaseBoard-Token": S.SHARED_SECRET}
        base = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession() as http:
            codes = []
            for _ in range(5):
                async with http.get(f"{base}/oauth/client-id", headers=headers) as r:
                    codes.append(r.status); last_headers = r.headers
            check("route /oauth limitée à 3/min/IP : 200,200,200 puis 429", codes == [200, 200, 200, 429, 429], codes)
            check("429 avec Retry-After", int(last_headers.get("Retry-After", 0)) >= 1)
            async with http.get(f"{base}/connect/inconnu") as r:
                check("/connect a son propre compteur (404 normal, pas 429)", r.status == 404, r.status)
            statuses = set()
            for _ in range(30):
                async with http.get(f"{base}/health") as r: statuses.add(r.status)
                async with http.get(f"{base}/activity", headers=headers) as r: statuses.add(r.status)
            check("/health et /activity jamais freinés (30 appels chacun)", 429 not in statuses, statuses)
            async with http.post(f"{base}/oauth/exchange", headers=headers, json={}) as r:
                check("la limite par IP s'applique aussi à /oauth/exchange (même route de base)", r.status == 429, r.status)
        await bot._web_runner.cleanup()
        check("limite active : le middleware est bien branché", True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port2 = sock.getsockname()[1]
    S.HTTP_PORT = port2
    with Limits(RATE_LIMITER=S.RateLimiter(0)):
        await bot._start_http_server()
        async with aiohttp.ClientSession() as http:
            codes = set()
            for _ in range(20):
                async with http.get(f"http://127.0.0.1:{port2}/oauth/client-id", headers={"X-WaseBoard-Token": S.SHARED_SECRET}) as r: codes.add(r.status)
            check("limite à 0 : aucune limitation (20 appels, aucun 429)", codes == {200}, codes)
        await bot._web_runner.cleanup()

    print("\n--- /instance et statistiques ---")
    check("format_bytes", S.format_bytes(0) == "0 octets" and S.format_bytes(1536) == "1.5 Ko" and S.format_bytes(5 * 1024**3) == "5.0 Go" and S.format_bytes(2 * 1024**4) == "2048.0 Go", [S.format_bytes(x) for x in (0, 1536, 5 * 1024**3)])
    check("format_duration", S.format_duration(30) == "0 min" and S.format_duration(3 * 3600 + 5 * 60) == "3 h 05 min" and S.format_duration(2 * 86400 + 3 * 3600) == "2 j 3 h" and S.format_duration(12 * 60) == "12 min")
    check("plafonds actifs : « aucun » par défaut", "aucun" in S.active_limits_text())
    with Limits(MAX_GUILDS=50, MAX_CONCURRENT_VOICE=20, MAX_FFMPEG_PROCESSES=40, HTTP_RATE_LIMIT_PER_MIN=60,
                GUILD_LIMIT_CEILINGS={"max_total_mb": 500}, DEFAULT_GUILD_LIMITS={"max_sounds": 200, "max_sources_per_guild": 8}):
        t = S.active_limits_text()
        check("plafonds actifs listés (max_sources_per_guild n'y figure plus : c'est un réglage de guilde, pas un plafond d'instance)",
              all(x in t for x in ("serveurs : 50", "salons vocaux : 20", "sons au total : 40", "60/min/IP", "max_total_mb=500", "max_sounds=200", "max_sources_per_guild=8"))
              and "sons/serveur" not in t, t)
    fresh_mixers()
    bot.mixers[G1.id].add("a", ("x", 1)); bot.mixers[G1.id].add("b", ("x", 2))
    bot.online_users = {"1": {"username": "u", "avatar_url": "", "last_seen": time.time()}}
    fake_list2 = [G1, G2, G3]
    S.WaseBoardServer.guilds = property(lambda self: fake_list2)
    try:
        stats = await bot.instance_stats()
        check("statistiques : serveurs, vocaux, sons en lecture, utilisateurs en ligne",
              stats["guilds"] == 3 and stats["voice"] == 2 and stats["sources"] == 2 and stats["online_users"] == 1, stats)
        check("statistiques : catalogue, octets, disque libre, serveurs les plus lourds",
              stats["catalog_sounds"] == len(S.load_catalog()) and stats["bytes_total"] > 0 and stats["disk_free"] > 0
              and stats["top_guilds"] and stats["top_guilds"][0]["bytes"] >= stats["top_guilds"][-1]["bytes"], stats)
        text = S.render_instance_stats(stats)
        check("rendu lisible (< 2000 caractères) avec les rubriques attendues", len(text) < 2000 and all(x in text for x in ("Instance WaseBoard", "Serveurs Discord : 3", "Salons vocaux actifs", "Catalogue", "Disque", "Plafonds actifs")), text)
        with Limits(MAX_GUILDS=10):
            check("capacité affichée « 3 / 10 »", "Serveurs Discord : 3 / 10" in S.render_instance_stats(stats))

        class FakeResponse:
            def __init__(self): self.sent, self.deferred = [], None
            async def send_message(self, content=None, **kw): self.sent.append((content, kw))
            async def defer(self, **kw): self.deferred = kw
        class FakeFollowup:
            def __init__(self): self.sent = []
            async def send(self, content=None, **kw): self.sent.append((content, kw))
        async def is_owner(user): return user.id == 1
        bot.is_owner = is_owner
        inter = SimpleNamespace(user=SimpleNamespace(id=5), response=FakeResponse(), followup=FakeFollowup())
        await S.instance_command.callback(inter)
        check("/instance : refusée à qui n'est pas propriétaire de l'application", "réservée" in inter.response.sent[0][0] and inter.response.deferred is None)
        inter = SimpleNamespace(user=SimpleNamespace(id=1), response=FakeResponse(), followup=FakeFollowup())
        await S.instance_command.callback(inter)
        check("/instance : propriétaire -> statistiques, éphémères", "Instance WaseBoard" in inter.followup.sent[0][0] and inter.followup.sent[0][1].get("ephemeral") is True)
        check("/instance masquée aux non-admins dans l'interface Discord", S.instance_command.default_permissions.administrator)

        async def fake_run_checks(config, data_dir, session, with_network=True, with_public_check=True):
            return [D.Check("ok", "Faux", "")], "1"
        real = D.run_checks; D.run_checks = fake_run_checks
        try:
            class FakeDM(discord.Member):
                voice = None
                def __init__(self): pass
                @property
                def guild_permissions(self): return Perms(True)
            for limit, expected in ((3, "warn"), (0, "info")):
                with Limits(MAX_GUILDS=limit):
                    inter = SimpleNamespace(user=FakeDM(), guild=G1, response=FakeResponse(), followup=FakeFollowup())
                    G1.voice_channels = []; G1.me = None
                    await S.diagnostic.callback(inter)
                    text = inter.followup.sent[0][0]
                    if limit:
                        ok = "Capacité de l'instance" in text
                    else:
                        ok = "Serveurs connectés" in text and "Capacité de l'instance" not in text
                    check(f"/diagnostic : {'⚠️ instance pleine' if limit else 'ℹ️ nombre de serveurs'} (max_guilds={limit})", ok, text)
        finally:
            D.run_checks = real
    finally:
        del S.WaseBoardServer.guilds

    print()
    print(f"{sum(results)}/{len(results)} vérifications OK")
    sys.exit(0 if all(results) else 1)


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir()
for leftover in (S.GUILD_SETTINGS_PATH, S.AUDIT_PATH, S.STATS_PATH, S.SHARED_CATEGORIES_PATH):
    leftover.unlink(missing_ok=True)
asyncio.run(main())
