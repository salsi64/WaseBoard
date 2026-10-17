"""Commandes Discord /play, /stop, /top et /bilan (+ autocomplétion des sons). Vrai code de server.py et vraie lecture
des fichiers, faux objets Discord. Dossier de données isolé (_env.py)."""
import asyncio
import json
import math
import shutil
import struct
import sys
import time
import wave
from datetime import datetime
from types import SimpleNamespace

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import audio as A  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


# ---------- faux Discord ----------
class Perms:
    def __init__(self, administrator=False): self.administrator = administrator

class Guild:
    def __init__(self, gid, owner_id, name):
        self.id, self.owner_id, self.roles, self.name, self.icon, self.members = gid, owner_id, [], name, None, {}
    def get_member(self, uid): return self.members.get(uid)
    def get_role(self, rid): return None

class PlainMember:
    def __init__(self, uid):
        self.id, self.guild_permissions, self.roles, self.display_name = uid, Perms(False), [], f"user{uid}"

G1, G2 = Guild(3001, 1, "Alpha"), Guild(3002, 2, "Beta")
for g, uids in ((G1, (1, 20, 21)), (G2, (2,))):
    for uid in uids:
        g.members[uid] = PlainMember(uid)
GUILDS = [G1, G2]
bot = S.bot


async def fake_memberships(user_id):
    return [(g, g.members[user_id]) for g in GUILDS if user_id in g.members]

bot.find_memberships = fake_memberships
bot.get_guild = lambda gid: next((g for g in GUILDS if g.id == gid), None)

ACTIVITY = []
async def record(sound_id, user_id, guild_id, ttl=None):
    ACTIVITY.append((sound_id, user_id, guild_id))
bot._record_play_activity = record


class FakeFF(A.discord.AudioSource):
    def __init__(self, path, **kw): pass
    def read(self): return A.SILENCE
    def cleanup(self): pass

S.discord.FFmpegPCMAudio = lambda path, **kw: FakeFF(path, **kw)
S.GUILD_ID = None


class Channel:
    def __init__(self, cid, name, guild): self.id, self.name, self.guild, self.members = cid, name, guild, []

class VC:
    def __init__(self, channel): self.channel = channel
    def is_connected(self): return True

CH_A, CH_B = Channel(11, "salon-a", G1), Channel(12, "salon-b", G1)
JOINED = []


async def fake_join(guild_id, channel):
    JOINED.append((guild_id, channel.name))
    vc = VC(channel)
    bot.voice_clients_map[guild_id] = vc
    bot.mixers[guild_id] = A.MixingAudioSource()
    return vc

bot.join_guild_voice = fake_join


def make_member(uid, channel=None):
    """Un vrai discord.Member (la commande vérifie isinstance) dont on fixe l'identité et le salon vocal."""
    cls = type(f"M{uid}", (S.discord.Member,), {"id": uid, "voice": SimpleNamespace(channel=channel) if channel else None,
                                                "display_name": f"user{uid}", "__init__": lambda self: None})
    return cls()


class Response:
    def __init__(self): self.sent, self.deferred = [], None
    async def send_message(self, content=None, **kw): self.sent.append((content, kw))
    async def defer(self, **kw): self.deferred = kw

class Followup:
    def __init__(self): self.sent = []
    async def send(self, content=None, **kw): self.sent.append((content, kw))


class Inter:
    def __init__(self, member, guild=G1):
        self.user, self.guild, self.guild_id = member, guild, guild.id
        self.response, self.followup = Response(), Followup()

    def texts(self):
        out = [c for c, _ in self.response.sent + self.followup.sent if c]
        return " | ".join(out)


def make_wav(path, seconds=1.0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(int(48000 * seconds))))


shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir(exist_ok=True)
G1_ID, G2_ID = str(G1.id), str(G2.id)
CATALOG = [
    {"id": "a", "name": "Applaudissements", "emoji": "👏", "extension": ".wav", "hash": "ha", "guild_id": G1_ID},
    {"id": "b", "name": "Bruh", "emoji": "💀", "extension": ".wav", "hash": "hb", "guild_id": G1_ID},
    {"id": "c", "name": "Boum", "extension": ".wav", "hash": "hc", "guild_id": G1_ID},
    {"id": "d", "name": "Boum", "extension": ".wav", "hash": "hd", "guild_id": G2_ID},       # même nom dans un autre serveur
    {"id": "z", "name": "Ailleurs", "extension": ".wav", "hash": "hz", "guild_id": G2_ID},
    {"id": "gone", "name": "Fichier manquant", "extension": ".wav", "hash": "hg", "guild_id": G1_ID},
]
S.save_catalog(CATALOG)
for entry in CATALOG:
    if entry["id"] != "gone":
        make_wav(S.SOUNDS_DIR / f"{entry['id']}{entry['extension']}")

# Le serveur 3001 (G1) partage « Ailleurs » (z) avec ses membres : visible sans en être natif.
S.save_shared_categories({G1_ID: ["z"]})


def reset():
    bot.voice_clients_map.clear(); bot.mixers.clear(); bot._pending_steps.clear(); bot._play_history.clear()
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS})
    JOINED.clear(); ACTIVITY.clear()


async def main():
    print("\n--- Autocomplétion ---")
    inter = Inter(make_member(20))
    choices = await S.sound_autocomplete(inter, "")
    names = [c.name for c in choices]
    check("membre de Alpha : ses sons + le son partagé, rien d'un serveur inconnu",
          sorted(c.value for c in choices) == ["a", "b", "c", "gone", "z"], [c.value for c in choices])
    check("valeur = identifiant du son, libellé = emoji + nom", ("👏 Applaudissements" in names) and all(c.value for c in choices), names)
    check("sans saisie : sons du serveur courant d'abord (ordre alphabétique), puis le son partagé",
          [c.value for c in choices] == ["a", "c", "b", "gone", "z"], [c.value for c in choices])
    choices = await S.sound_autocomplete(inter, "bo")
    check("« bo » : seul Boum correspond (Bruh ne contient pas « bo »)", [c.value for c in choices] == ["c"], [c.value for c in choices])
    choices = await S.sound_autocomplete(inter, "u")
    check("« u » : ceux qui COMMENCENT par u d'abord… (aucun ici), puis les autres par ordre alphabétique",
          [c.value for c in choices] == ["a", "c", "b", "gone", "z"], [c.value for c in choices])
    S.save_catalog(CATALOG + [{"id": "q", "name": "Un son", "extension": ".wav", "hash": "hq", "guild_id": G1_ID}])
    choices = await S.sound_autocomplete(inter, "u")
    check("… et dès que l'un commence par la saisie, il passe devant", [c.value for c in choices][0] == "q", [c.value for c in choices])
    S.save_catalog(CATALOG)
    choices = await S.sound_autocomplete(inter, "RU")
    check("recherche au milieu du nom, sans tenir compte de la casse", [c.value for c in choices] == ["b"], [c.value for c in choices])
    check("aucune correspondance : liste vide", await S.sound_autocomplete(inter, "zzz") == [])
    check("membre d'un autre serveur : voit les siens seulement", sorted(c.value for c in await S.sound_autocomplete(Inter(make_member(2), G2), "")) == ["d", "z"])
    check("quelqu'un qui n'est membre d'aucun serveur : rien (jamais le catalogue entier)", await S.sound_autocomplete(Inter(make_member(99)), "") == [])
    big = [{"id": f"x{i}", "name": f"Sample {i:02d}", "extension": ".wav", "hash": f"h{i}", "guild_id": G1_ID} for i in range(40)]
    S.save_catalog(CATALOG + big)
    check("25 propositions au plus (limite de Discord)", len(await S.sound_autocomplete(inter, "sample")) == 25)
    S.save_catalog([{**e, "name": "N" * 150} if e["id"] == "a" else e for e in CATALOG])
    check("libellé tronqué à 100 caractères (limite de Discord)", all(len(c.name) <= 100 for c in await S.sound_autocomplete(inter, "")))
    S.save_catalog(CATALOG)

    print("\n--- Retrouver un son tapé à la main ---")
    cat = [e for e in CATALOG if e["guild_id"] == G1_ID]
    find = S.find_sound_for_command
    check("identifiant exact", find("b", cat)[0]["name"] == "Bruh")
    check("nom exact, sans tenir compte de la casse", find("bRuH", cat)[0]["id"] == "b")
    check("morceau de nom sans ambiguïté", find("applaud", cat)[0]["id"] == "a")
    entry, message = find("applaud", cat + [{"id": "x1", "name": "Applaudissements 2", "extension": ".wav", "guild_id": G1_ID}])
    check("plusieurs correspondances : message qui renvoie vers la liste", entry is None and "Plusieurs" in message, message)
    entry, message = find("ru", cat)
    check("moins de 3 lettres : pas de recherche partielle (« ru » ne joue pas Bruh)", entry is None and "Aucun son" in message, message)
    entry, message = find("inconnu", cat)
    check("aucune correspondance : message", entry is None and "Aucun son" in message, message)
    check("saisie vide : message, pas de son au hasard", find("   ", cat)[0] is None)
    check("deux sons de même nom (dans des serveurs différents) : ambigu", find("boum", cat + [CATALOG[3]])[0] is None)

    print("\n--- /play ---")
    reset()
    inter = Inter(make_member(20))
    await S.play_command.callback(inter, "a")
    check("pas dans un salon vocal : message, rien ne joue", "connecté à un salon vocal" in inter.texts() and not bot.mixers and not JOINED)
    check("…et le message est éphémère", all(kw.get("ephemeral") is True for _, kw in inter.response.sent))

    reset()
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "a")
    mixer = bot.mixers.get(G1.id)
    check("le bot rejoint le salon de la personne puis joue le son", JOINED == [(G1.id, "salon-a")] and mixer is not None and mixer.has("a"), (JOINED, mixer))
    check("réponse éphémère avec l'emoji et le nom", inter.followup.sent and "Applaudissements" in inter.followup.sent[0][0] and inter.followup.sent[0][1].get("ephemeral") is True, inter.followup.sent)
    check("la commande a d'abord accusé réception (le salon peut mettre du temps à être rejoint)", inter.response.deferred == {"ephemeral": True, "thinking": True}, inter.response.deferred)
    await asyncio.sleep(0.05)
    check("l'activité (avatar, statistiques) est enregistrée au nom de la personne", ("a", "20", G1.id) in ACTIVITY, ACTIVITY)

    inter = Inter(make_member(21, CH_A))
    await S.play_command.callback(inter, "Bruh")
    check("déjà connecté au même salon : pas de nouvelle connexion, le son s'ajoute (nom tapé)", JOINED == [(G1.id, "salon-a")] and mixer.has("b") and mixer.has("a"), JOINED)

    inter = Inter(make_member(21, CH_B))
    await S.play_command.callback(inter, "c")
    check("le bot est dans un autre salon : on le dit, on ne le déplace pas, rien ne joue",
          "salon-a" in inter.texts() and "/join" in inter.texts() and not mixer.has("c") and JOINED == [(G1.id, "salon-a")], inter.texts())

    reset()
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "d")
    check("son d'un serveur dont on n'est pas membre : introuvable (jamais joué)", "Aucun son" in inter.texts() and not JOINED, inter.texts())
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "z")
    check("son partagé avec son serveur : jouable", bot.mixers[G1.id].has("z"), inter.texts())
    reset()
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "gone")
    check("fichier absent sur le disque : message clair, le bot ne rejoint pas pour rien", "introuvable" in inter.texts() and not JOINED, inter.texts())
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "boum")
    check("« boum » : le Boum de MON serveur (celui de l'autre serveur n'est pas en concurrence)", bot.mixers[G1.id].has("c") and not bot.mixers[G1.id].has("d"), inter.texts())

    reset()
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS, "play_rate_per_min": 1})
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "a")
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "b")
    check("anti-spam du serveur appliqué comme pour l'API : 2e son refusé avec le délai", "⚠️" in inter.texts() and "Trop de sons" in inter.texts() and not bot.mixers[G1.id].has("b"), inter.texts())

    reset()
    S.save_guild_settings(G1.id, {**S.DEFAULT_GUILD_SETTINGS, "max_sources_per_guild": 1})
    await S.play_command.callback(Inter(make_member(20, CH_A)), "a")
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "b")
    check("plafond de sons simultanés appliqué", "⚠️" in inter.texts() and "maximum 1" in inter.texts() and not bot.mixers[G1.id].has("b"), inter.texts())
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "a")
    check("rejouer le son déjà en cours (il est remplacé) reste permis", "⚠️" not in inter.texts())

    reset()
    async def full(guild_id, channel): raise S.CapacityError("Ce serveur WaseBoard est saturé")
    bot.join_guild_voice = full
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "a")
    check("instance saturée : message ⚠️ lisible", "⚠️" in inter.texts() and "saturé" in inter.texts(), inter.texts())
    async def broken(guild_id, channel): raise RuntimeError("pas la permission")
    bot.join_guild_voice = broken
    inter = Inter(make_member(20, CH_A))
    await S.play_command.callback(inter, "a")
    check("connexion vocale impossible : message ❌ au lieu d'une erreur muette", "❌" in inter.texts() and "Se connecter" in inter.texts(), inter.texts())
    bot.join_guild_voice = fake_join

    check("/play : réservée aux serveurs, avec autocomplétion sur « son »", S.play_command.guild_only and S.play_command._params["son"].autocomplete is S.sound_autocomplete)

    print("\n--- /stop ---")
    reset()
    inter = Inter(make_member(20))
    await S.stop_command.callback(inter)
    check("bot absent du vocal : message", "aucun salon vocal" in inter.texts())
    await S.play_command.callback(Inter(make_member(20, CH_A)), "a")
    await S.play_command.callback(Inter(make_member(20, CH_A)), "b")
    mixer = bot.mixers[G1.id]
    pending_handle = asyncio.get_running_loop().call_later(60, lambda: None)
    bot._pending_steps[G1.id] = {pending_handle: "c"}  # une étape d'enchaînement pas encore partie
    inter = Inter(make_member(21))
    await S.stop_command.callback(inter)
    check("/stop : tous les sons coupés (même par quelqu'un qui n'est pas dans le salon : comme le bouton Stop de l'appli)", not mixer.has("a") and not mixer.has("b") and "coupés" in inter.texts(), inter.texts())
    check("/stop : les étapes d'enchaînement pas encore parties sont annulées aussi", pending_handle.cancelled() and not bot._pending_steps[G1.id])
    await S.play_command.callback(Inter(make_member(20, CH_A)), "a")
    await S.play_command.callback(Inter(make_member(20, CH_A)), "b")
    inter = Inter(make_member(20))
    await S.stop_command.callback(inter, "a")
    check("/stop son:a : seulement celui-là", not mixer.has("a") and mixer.has("b") and "Applaudissements" in inter.texts(), inter.texts())
    inter = Inter(make_member(20))
    await S.stop_command.callback(inter, "a")
    check("couper un son qui ne joue pas : le dit", "ne jouait pas" in inter.texts(), inter.texts())
    inter = Inter(make_member(20))
    await S.stop_command.callback(inter, "inexistant")
    check("son inconnu : message", "Aucun son" in inter.texts() and mixer.has("b"), inter.texts())
    check("/stop : réservée aux serveurs, autocomplétion sur « son »", S.stop_command.guild_only and S.stop_command._params["son"].autocomplete is S.sound_autocomplete)

    print("\n--- /top et /bilan ---")
    now = time.time()
    year = datetime.now().year
    def line(ts, uid, sid, guild=G1_ID, name="x"):
        return json.dumps({"ts": ts, "user_id": uid, "username": f"user{uid}", "sound_id": sid, "sound_name": name, "emoji": None,
                           "guild_id": guild, "guild_name": "g"}, ensure_ascii=False)
    lines = ([line(now - 3600, "20", "a")] * 6 + [line(now - 7200, "21", "b")] * 3 + [line(now - 40 * 86400, "20", "b")] * 2
             + [line(now - 3600, "2", "z", guild=G2_ID)] * 4)
    S.STATS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    inter = Inter(make_member(20))
    await S.top_command.callback(inter)
    embed = inter.followup.sent[0][1]["embed"]
    fields = {f.name: f.value for f in embed.fields}
    check("/top : réponse privée par défaut", inter.response.deferred == {"ephemeral": True, "thinking": True}, inter.response.deferred)
    check("/top : serveur et période (30 jours) dans le titre", "Alpha" in embed.title and "30 derniers jours" in embed.title, embed.title)
    check("/top : classement des sons du serveur, sans ceux de l'autre serveur ni des 40 jours précédents",
          "Applaudissements" in fields["🔊 Sons les plus joués"] and "— 6" in fields["🔊 Sons les plus joués"]
          and "Bruh** — 3" in fields["🔊 Sons les plus joués"] and "Ailleurs" not in fields["🔊 Sons les plus joués"], fields)
    check("/top : total de lectures", "**9**" in embed.description, embed.description)
    check("/top : membres", "user20" in fields["👤 Membres les plus actifs"] and "user21" in fields["👤 Membres les plus actifs"])
    inter = Inter(make_member(20))
    await S.top_command.callback(inter, 365, True)
    check("/top partager:oui — visible de tous ; période 12 mois (les 2 lectures de 40 jours comptent)",
          inter.response.deferred == {"ephemeral": False, "thinking": True} and "12 derniers mois" in inter.followup.sent[0][1]["embed"].title
          and "**11**" in inter.followup.sent[0][1]["embed"].description, inter.response.deferred)
    inter = Inter(make_member(2), G2)
    await S.top_command.callback(inter, 7)
    check("/top : chaque serveur voit le sien", "**4**" in inter.followup.sent[0][1]["embed"].description and "Beta" in inter.followup.sent[0][1]["embed"].title)
    S.STATS_PATH.write_text("", encoding="utf-8")
    inter = Inter(make_member(20))
    await S.top_command.callback(inter)
    check("/top : journal vide -> « Aucun son joué »", "Aucun son" in inter.followup.sent[0][1]["embed"].description)
    S.STATS_PATH.unlink()
    inter = Inter(make_member(20))
    await S.top_command.callback(inter)
    check("/top : pas de journal du tout -> pareil, sans erreur", "Aucun son" in inter.followup.sent[0][1]["embed"].description)
    real_agg = S.aggregate_play_stats
    S.aggregate_play_stats = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disque illisible"))
    try:
        inter = Inter(make_member(20))
        await S.top_command.callback(inter)
        check("/top : panne de calcul -> message ❌ éphémère (pas d'interaction qui expire)", "❌" in inter.texts() and inter.followup.sent[0][1].get("ephemeral") is True, inter.texts())
    finally:
        S.aggregate_play_stats = real_agg
    check("/top : réservée aux serveurs ; 4 périodes proposées",
          S.top_command.guild_only and [c.value for c in S.top_command._params["periode"].choices] == [7, 30, 90, 365])

    mid = datetime(year, 6, 15, 21, 0).timestamp()
    S.STATS_PATH.write_text("\n".join([line(mid, "20", "a")] * 5 + [line(mid, "21", "b")] * 2 + [line(mid, "2", "z", guild=G2_ID)]
                                      + [line(datetime(year - 1, 6, 1).timestamp(), "20", "a")]) + "\n", encoding="utf-8")
    inter = Inter(make_member(20))
    await S.bilan_command.callback(inter)
    embed = inter.followup.sent[0][1]["embed"]
    fields = {f.name: f.value for f in embed.fields}
    check("/bilan : année en cours par défaut, privé", str(year) in embed.title and inter.response.deferred == {"ephemeral": True, "thinking": True}, embed.title)
    check("/bilan : totaux du serveur seulement", "**7** son(s)" in embed.description, embed.description)
    check("/bilan : « Vous » = la personne qui demande", "**5** son(s)" in fields["🙋 Vous"] and "**#1** sur 2" in fields["🙋 Vous"], fields["🙋 Vous"])
    other = Inter(make_member(21))
    await S.bilan_command.callback(other)
    check("/bilan : un autre membre voit SA part", "**2** son(s)" in {f.name: f.value for f in other.followup.sent[0][1]["embed"].fields}["🙋 Vous"])
    inter = Inter(make_member(20))
    await S.bilan_command.callback(inter, year - 1, True)
    check("/bilan année passée, partagé", "**1** son(s)" in inter.followup.sent[0][1]["embed"].description and inter.response.deferred["ephemeral"] is False)
    inter = Inter(make_member(20))
    await S.bilan_command.callback(inter, 2021)
    check("/bilan année sans données : message simple", "Aucun son joué en 2021" in inter.followup.sent[0][1]["embed"].description)
    real_report = S.reports.year_report
    S.reports.year_report = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boum"))
    try:
        inter = Inter(make_member(20))
        await S.bilan_command.callback(inter)
        check("/bilan : panne de calcul -> message ❌ éphémère", "❌" in inter.texts() and inter.followup.sent[0][1].get("ephemeral") is True, inter.texts())
    finally:
        S.reports.year_report = real_report
    check("/bilan : réservée aux serveurs", S.bilan_command.guild_only)
    S.STATS_PATH.unlink()

    print("\n--- Enregistrement des commandes ---")
    registered = {c.name for c in bot.tree.get_commands()}
    check("play, stop, top, bilan sont déclarées (et les anciennes aussi)", {"play", "stop", "top", "bilan", "join", "leave", "diagnostic", "instance"} <= registered, registered)
    check("aucun nom de commande en double", len(bot.tree.get_commands()) == len(registered))


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
