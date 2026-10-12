"""Options de lecture de POST /play (boucle, effets), POST /stop d'un seul son, POST /play-sequence
(enchaînements) et leurs garde-fous. Vrai code de server.py et vrai ffmpeg (boucles et effets décodés pour de bon),
faux objets Discord. Dossier de données isolé (_env.py)."""
import asyncio
import json
import math
import shutil
import struct
import sys
import time
import wave

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import audio as A  # noqa: E402
import server as S  # noqa: E402
import wb_config  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


# ---------- faux Discord (mêmes principes que test_capacity.py) ----------
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

G1, G2 = Guild(2001, 1, "Alpha"), Guild(2002, 2, "Beta")
for g, members in ((G1, [(1, False), (20, False), (21, False)]), (G2, [(2, False)])):
    for uid, adm in members:
        g.members[uid] = Member(uid, adm)
GUILDS = [G1, G2]
bot = S.bot


async def fake_memberships(user_id):
    return [(g, g.members[user_id]) for g in GUILDS if user_id in g.members]

bot.find_memberships = fake_memberships
bot.get_guild = lambda gid: next((g for g in GUILDS if g.id == gid), None)

ACTIVITY = []
async def record(sound_id, user_id, guild_id, ttl=None):
    ACTIVITY.append((sound_id, user_id, ttl))
bot._record_play_activity = record
S.record_play_stat = lambda *a, **k: None

FF_CALLS = []

class FakeFF(A.discord.AudioSource):
    """Remplace ffmpeg pour la lecture en flux : des trames sans fin, et la trace des arguments reçus."""
    def __init__(self, path, **kw):
        FF_CALLS.append((path, kw))
    def read(self): return A.SILENCE
    def cleanup(self): pass

S.discord.FFmpegPCMAudio = lambda path, **kw: FakeFF(path, **kw)

for uid in (1, 20, 21, 2):
    bot.oauth_sessions[f"tok{uid}"] = {
        "user_id": str(uid), "username": f"user{uid}", "avatar_url": None,
        "discord_access_token": "x", "discord_refresh_token": "y",
        "discord_token_expires_at": time.time() + 10**6, "created_at": 0, "last_used_at": 0,
    }


class Req:
    def __init__(self, uid, body=None):
        self.headers = {"X-WaseBoard-Token": S.SHARED_SECRET, "Authorization": f"Bearer tok{uid}"}
        self.query, self.match_info, self._body = {}, {}, body
    async def json(self): return self._body


async def call(handler, uid, body=None):
    resp = await handler(Req(uid, body))
    try:
        payload = json.loads(resp.text)
    except Exception:
        payload = None
    return resp.status, payload


S.SHARED_SECRET = "s" * 40
S.GUILD_ID = None

# ---------- sons de test : de vrais fichiers (boucles et effets passent par le vrai ffmpeg) ----------
shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir(exist_ok=True)
G1_ID = str(G1.id)


def make_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(int(48000 * seconds))))


CATALOG = [
    {"id": "a", "name": "alpha", "extension": ".wav", "hash": "ha", "guild_id": G1_ID},
    {"id": "b", "name": "beta", "extension": ".wav", "hash": "hb", "guild_id": G1_ID},
    {"id": "c", "name": "court", "extension": ".wav", "hash": "hc", "guild_id": G1_ID, "trim_start_ms": 0, "trim_end_ms": 300},
    {"id": "z", "name": "ailleurs", "extension": ".wav", "hash": "hz", "guild_id": str(G2.id)},
]
S.save_catalog(CATALOG)
for entry in CATALOG:
    make_wav(S.SOUNDS_DIR / f"{entry['id']}{entry['extension']}", 1.0)


class VC:
    """Faux client vocal : le salon contient les membres 1, 20 et 21 (c'est ainsi que le serveur retrouve la guilde de l'appelant)."""
    channel = type("Channel", (), {"members": [Member(1), Member(20), Member(21)], "name": "salon"})()
    def is_connected(self): return True


def reset():
    bot.voice_clients_map = {G1.id: VC()}
    bot.mixers = {G1.id: A.MixingAudioSource()}
    bot._pending_steps.clear()
    bot._play_history.clear()
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS})
    FF_CALLS.clear(); ACTIVITY.clear()
    return bot.mixers[G1.id]


class Settings:
    """Pose une valeur de configuration le temps d'un bloc."""
    def __init__(self, **values): self.values, self.saved = values, {}
    def __enter__(self):
        for k, v in self.values.items(): self.saved[k] = getattr(S, k); setattr(S, k, v)
    def __exit__(self, *a):
        for k, v in self.saved.items(): setattr(S, k, v)


def frames(mixer, n):
    return [mixer.read() for _ in range(n)]


async def main():
    print("\n--- Lecture simple : inchangée ---")
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "a"})
    check("clic simple : 200, ms, son dans le mixeur, ffmpeg en flux sans filtre", st == 200 and "ms" in p and mixer.has("a")
          and len(FF_CALLS) == 1 and "-af" not in (FF_CALLS[0][1].get("options") or ""), (st, p, FF_CALLS))
    st, p = await call(bot._handle_play, 20, {"id": "a", "volume": "fort"})
    check("volume invalide : 400 lisible (et non plus une erreur 500)", st == 400 and "volume" in p["error"], (st, p))
    st, p = await call(bot._handle_play, 20, [1])
    check("corps JSON qui n'est pas un objet : 400", st == 400, (st, p))

    print("\n--- Boucle ---")
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "a", "loop": True})
    check("boucle acceptée (le son est décodé pour de bon)", st == 200 and mixer.has("a") and not FF_CALLS, (st, p))
    out = frames(mixer, 130)                       # 2,6 s : le son dure 1 s
    check("2,6 s plus tard, un son de 1 s joue toujours : la boucle recommence sans fin", mixer.has("a") and all(f != A.SILENCE for f in out))
    await asyncio.sleep(0.05)                      # laisse partir l'enregistrement d'activité (tâche d'arrière-plan)
    check("le highlight de la boucle dure aussi longtemps que la boucle (pas 30 s)", ("a", "20", S.LOOP_MAX_MINUTES * 60 + 5) in ACTIVITY, ACTIVITY)
    st, p = await call(bot._handle_stop_all, 20, {"sound_id": "a"})
    check("/stop {sound_id} arrête la boucle (stopped: true)", st == 200 and p == {"status": "ok", "stopped": True} and not mixer.has("a"), (st, p))
    st, p = await call(bot._handle_stop_all, 20, {"sound_id": "a"})
    check("arrêter un son qui ne joue pas : ok, stopped: false", st == 200 and p["stopped"] is False, p)

    mixer = reset()
    with Settings(LOOP_MAX_MINUTES=0.02):          # 1,2 s = 60 trames
        await call(bot._handle_play, 20, {"id": "a", "loop": True})
        frames(mixer, 70)
    check("plafond de durée (loop_max_minutes) : la boucle s'arrête toute seule", not mixer.has("a"))
    with Settings(LOOP_MAX_MINUTES=0):
        st, p = await call(bot._handle_play, 20, {"id": "a", "loop": True})
        check("loop_max_minutes = 0 : boucles refusées (400)", st == 400 and "désactivées" in p["error"], (st, p))
        st, p = await call(bot._handle_play, 20, {"id": "a"})
        check("…mais la lecture simple continue de marcher", st == 200)
    mixer = reset()
    with Settings(LOOP_MAX_BYTES=1000):
        st, p = await call(bot._handle_play, 20, {"id": "a", "loop": True})
        check("son trop long pour une boucle : 400 lisible, rien n'est lancé", st == 400 and "boucle" in p["error"] and not mixer.has("a"), (st, p))
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "c", "loop": True})
    out = frames(mixer, 60)
    check("boucle d'un son découpé : joue la portion gardée sans fin", st == 200 and mixer.has("c") and all(f != A.SILENCE for f in out), (st, p))
    mixer = reset()
    await call(bot._handle_play, 20, {"id": "a", "loop": True, "effects": {"speed": 2.0}})
    frames(mixer, 80)
    check("boucle + effets : décodée avec le filtre (1 s à vitesse x2 = 0,5 s, boucle toujours en cours)", mixer.has("a"))

    print("\n--- Effets ---")
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "a", "effects": {"pitch": 5, "echo": True}})
    options = FF_CALLS[0][1].get("options", "") if FF_CALLS else ""
    check("effets : lecture par ffmpeg avec la chaîne -af", st == 200 and "-af" in options and "asetrate" in options and "aecho" in options, (st, options))
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "c", "effects": {"speed": 1.5}})
    kw = FF_CALLS[0][1]
    check("effets sur un son découpé : découpe à l'entrée (-ss/-t dans before_options)", kw.get("before_options") == "-ss 0.000 -t 0.300", kw)
    for label, effects_ in (("effet inconnu", {"reverb": 1}), ("valeur hors bornes", {"pitch": 40}), ("pas un objet", "pitch")):
        mixer = reset()
        st, p = await call(bot._handle_play, 20, {"id": "a", "effects": effects_})
        check(f"{label} : 400 lisible, rien n'est lancé", st == 400 and p["error"] and not mixer.has("a") and not FF_CALLS, (st, p))
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "a", "effects": {"pitch": 0, "speed": 1}})
    check("effets neutres : lecture normale, aucun filtre", st == 200 and "-af" not in (FF_CALLS[0][1].get("options") or ""), FF_CALLS)

    print("\n--- Droits : les contrôles de /play s'appliquent partout ---")
    mixer = reset()
    st, p = await call(bot._handle_play, 20, {"id": "z", "loop": True})
    check("boucle d'un son d'un autre serveur : 403", st == 403 and not mixer.has("z"), (st, p))
    st, p = await call(bot._handle_play, 2, {"id": "a", "loop": True})
    check("appelant non membre du serveur ciblé : 403", st == 403, (st, p))
    bot.voice_clients_map = {}
    st, p = await call(bot._handle_play, 20, {"id": "a", "loop": True, "guild_id": G1_ID})
    check("bot hors vocal : 409", st == 409, (st, p))

    print("\n--- Enchaînements (/play-sequence) ---")
    mixer = reset()
    t0 = time.perf_counter()
    st, p = await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "b", "delay_ms": 200}, {"id": "c", "delay_ms": 200}]})
    check("séquence acceptée en un appel", st == 200 and p["status"] == "ok" and p["steps"] == 3, (st, p))
    check("la première étape part tout de suite, les suivantes sont programmées", mixer.has("a") and not mixer.has("b") and len(bot._pending_steps[G1.id]) == 2)
    await asyncio.sleep(0.3)
    check("200 ms plus tard : la deuxième est partie, pas la troisième", mixer.has("b") and not mixer.has("c"), (mixer.has("b"), mixer.has("c")))
    await asyncio.sleep(0.3)
    check("400 ms : les trois jouent ; plus rien en attente", mixer.has("c") and not bot._pending_steps[G1.id])
    check("chaque étape enregistre son activité (avatar/highlight)", {a[0] for a in ACTIVITY} == {"a", "b", "c"}, ACTIVITY)

    mixer = reset()
    st, p = await call(bot._handle_play_sequence, 20, {"steps": [{"id": "c"}, {"id": "b", "after_end": True, "delay_ms": 100}]})
    await asyncio.sleep(0.2)
    check("after_end : l'étape suivante attend la FIN du son précédent (300 ms) + 100 ms", st == 200 and not mixer.has("b"))
    await asyncio.sleep(0.4)
    check("…puis part", mixer.has("b"))

    mixer = reset()
    await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "b", "delay_ms": 500}]})
    st, p = await call(bot._handle_stop_all, 20, {})
    check("/stop (tout) annule aussi les étapes pas encore parties", st == 200 and not bot._pending_steps[G1.id] and not mixer.has("a"), (st, p))
    await asyncio.sleep(0.7)
    check("…elles ne partent jamais", not mixer.has("b"))
    mixer = reset()
    await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "b", "delay_ms": 400}, {"id": "c", "delay_ms": 400}]})
    await call(bot._handle_stop_all, 20, {"sound_id": "b"})
    await asyncio.sleep(0.6)
    check("/stop {sound_id} annule les étapes programmées de CE son seulement", not mixer.has("b") and len(bot._pending_steps[G1.id]) == 1, bot._pending_steps[G1.id])
    bot._cancel_pending_steps(G1.id)

    mixer = reset()
    cases = (("aucune étape", {"steps": []}), ("pas une liste", {"steps": "a"}), (f"trop d'étapes (> {S.SEQUENCE_MAX_STEPS})", {"steps": [{"id": "a"}] * (S.SEQUENCE_MAX_STEPS + 1)}),
             ("étape qui n'est pas un objet", {"steps": ["a"]}), ("délai négatif", {"steps": [{"id": "a", "delay_ms": -5}]}),
             ("délai trop long", {"steps": [{"id": "a", "delay_ms": S.SEQUENCE_MAX_DELAY_MS + 1}]}),
             ("délai texte", {"steps": [{"id": "a", "delay_ms": "x"}]}),
             ("effet invalide à l'étape 2", {"steps": [{"id": "a"}, {"id": "b", "effects": {"pitch": 99}}]}),
             ("durée totale trop longue", {"steps": [{"id": "a", "delay_ms": 10000}] * 7}),
             ("volume invalide", {"steps": [{"id": "a", "volume": "x"}]}))
    for label, body in cases:
        st, p = await call(bot._handle_play_sequence, 20, body)
        check(f"refusée : {label} (400), rien n'est lancé", st == 400 and not mixer.has("a") and not bot._pending_steps.get(G1.id), (st, p))
    st, p = await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "b", "effects": {"pitch": 99}}]})
    check("le message indique l'étape fautive", "Étape 2" in p["error"], p)
    st, p = await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "inconnu"}]})
    check("son inconnu dans la séquence : 404 et la première étape n'est PAS lancée (tout ou rien)", st == 404 and not mixer.has("a"), (st, p))
    st, p = await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "z"}]})
    check("son d'un serveur étranger dans la séquence : 403, rien n'est lancé", st == 403 and not mixer.has("a"), (st, p))
    bot.voice_clients_map = {}
    st, p = await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}], "guild_id": G1_ID})
    check("bot hors vocal : 409", st == 409, (st, p))

    print("\n--- Anti-spam : une séquence compte pour autant de lectures que d'étapes ---")
    mixer = reset()
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS, "play_rate_per_min": 4})
    s1 = (await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}))[0]
    s2 = (await call(bot._handle_play_sequence, 20, {"steps": [{"id": "a"}, {"id": "b"}]}))
    check("3 étapes passent (quota 4/min), 2 de plus dépasseraient : 429 sans rien lancer", s1 == 200 and s2[0] == 429 and "retry_after" in s2[1], (s1, s2))
    s3 = (await call(bot._handle_play, 20, {"id": "a"}))[0]
    s4 = (await call(bot._handle_play, 20, {"id": "a"}))[0]
    check("le quota restant (1) sert à un clic simple, le suivant est refusé", s3 == 200 and s4 == 429, (s3, s4))
    bot._cancel_pending_steps(G1.id)

    print("\n--- Réglages et fonctions annoncées ---")
    check("/status annonce loop, stop_sound, sequence, play_effects", {"loop", "stop_sound", "sequence", "play_effects"} <= set(S.SERVER_FEATURES), S.SERVER_FEATURES)
    check("réglage par défaut : boucles de 30 min au plus", S.LOOP_MAX_MINUTES == 30)
    bad_ok = True
    for key, values in (("loop_max_minutes", (-1, "x", True, 1000, None)),):
        for bad in values:
            try:
                wb_config.validate_limits({key: bad}); bad_ok = False
            except wb_config.ConfigError:
                pass
    check("valeurs invalides refusées au démarrage", bad_ok)
    wb_config.validate_limits({"loop_max_minutes": 0}); wb_config.validate_limits({"loop_max_minutes": 12.5})
    cfg = wb_config.load_config(environ={"WASEBOARD_LOOP_MAX_MINUTES": "10"}, config_path=_env.DATA_DIR / "inexistant.json")
    check("variable d'environnement WASEBOARD_LOOP_MAX_MINUTES prise en compte", cfg.get("loop_max_minutes") == 10.0, cfg)


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
