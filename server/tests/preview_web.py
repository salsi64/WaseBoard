"""Aperçu de la page web (PWA) avec de FAUSSES données, sans bot ni compte Discord : lance le vrai serveur HTTP de server.py
(vraies routes, vrais fichiers de web/, vraie authentification) sur un dossier de données jetable, avec trois serveurs Discord
fictifs, des sons de test, un salon vocal et une session déjà ouverte. Outil de développement — ce n'est pas un test.

Usage :  python tests/preview_web.py [--port 5008]
Puis ouvrir http://localhost:5008/app/ et, dans la console du navigateur, entrer la session d'aperçu :
    localStorage.setItem('wb.session', JSON.stringify({session_token: 'preview-session-token-0123456789abcdef', user_id: '20', username: 'Alice', avatar_url: null}))
(« localhost » est un contexte sécurisé : le service worker et l'installation fonctionnent, même sans HTTPS.)
"""
import argparse
import asyncio
import math
import struct
import sys
import time
import wave

import _env  # noqa: E402  (dossier de données jetable : jamais les vraies données)
import audio as A  # noqa: E402
import server as S  # noqa: E402

PORT = 5008
TOKEN = "preview-session-token-0123456789abcdef"


class Perms:
    def __init__(self, administrator=False): self.administrator = administrator

class Member:
    def __init__(self, uid, name, administrator=False):
        self.id, self.guild_permissions, self.roles, self.display_name = uid, Perms(administrator), [], name
        self.display_avatar = type("Avatar", (), {"url": f"https://cdn.discordapp.com/embed/avatars/{uid % 5}.png"})()
        self.bot = False

class Icon:
    def __init__(self, url): self.url = url

class Guild:
    def __init__(self, gid, name, owner_id):
        self.id, self.name, self.owner_id, self.roles, self.icon, self.members = gid, name, owner_id, [], None, {}
    def get_member(self, uid): return self.members.get(uid)
    def get_role(self, rid): return None

G1, G2, G3 = Guild(9001, "Alpha Squad", 1), Guild(9002, "Les Potes", 2), Guild(9003, "Un serveur au nom vraiment très long pour tester", 3)
ME = Member(20, "Alice")
FRIEND = Member(21, "Bastien")
ME_ADMIN = Member(20, "Alice", administrator=True)   # Alice est admin de « Les Potes » seulement
for guild in (G1, G2, G3):
    guild.members[20] = ME_ADMIN if guild is G2 else ME
    guild.members[21] = FRIEND
GUILDS = [G1, G2, G3]


class Channel:
    def __init__(self): self.name, self.members, self.id, self.guild = "Général", [ME, FRIEND], 1, G1

class VC:
    channel = Channel()
    def is_connected(self): return True

SOUNDS = [
    ("Applaudissements", "👏", G1), ("Victoire", "🏆", G1), ("Rire de sorcière", "🧙", G1), ("Alerte", "🚨", G1), ("Bruh", "💀", G1),
    ("Vine boom", "💥", G1), ("Oh non", "😱", G1), ("Sad violin", "🎻", G1), ("Éléphant qui barrit très fort dans la savane africaine", "🐘", G1),
    ("Tambour", "🥁", G2), ("Fanfare", "🎺", G2), ("Bravo", "🎉", G2), ("Cloche", "🔔", G2), ("Pet", "💨", G2), ("Sans emoji", "", G2),
    ("Ça roule", "🛞", G2), ("Moteur", "🏎️", G2), ("Explosion", "💣", G3), ("Laser", "🔫", G3), ("Magie", "✨", G3),
]


def make_wav(path, seconds, hz):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / 48000) * min(1, (seconds * 48000 - i) / 4800)))
                               for i in range(int(48000 * seconds))))


def seed():
    S.SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    catalog = []
    for index, (name, emoji, guild) in enumerate(SOUNDS):
        sound_id = f"s{index:02d}"
        # Alice est l'auteur de quelques sons (modifiables par elle) ; les autres sont ceux de Bastien (modifiables seulement chez « Les Potes », où elle est admin).
        author = "20" if name in ("Applaudissements", "Bruh", "Tambour") else "21"
        entry = {"id": sound_id, "name": name, "extension": ".wav", "hash": f"h{index}", "guild_id": str(guild.id), "uploaded_by": author}
        if emoji:
            entry["emoji"] = emoji
        if name == "Tambour":
            entry["trim_start_ms"], entry["trim_end_ms"] = 200, 900
        catalog.append(entry)
        make_wav(S.SOUNDS_DIR / f"{sound_id}.wav", 1.2 + (index % 4) * 0.7, 300 + index * 40)
    S.save_catalog(catalog)
    S.save_shared_categories({str(G2.id): ["s00", "s01"], str(G3.id): ["s10"]})   # sons partagés dans d'autres serveurs
    # Catégories personnelles d'Alice (copie envoyée par l'application Windows), mêlées aux serveurs dans l'ordre de l'application.
    S.save_user_categories({"20": {
        "categories": {"Réactions": ["s00", "s04", "s06", "s07"], "Fêtes": ["s09", "s10", "s11", "s17"], "Vide": ["supprimé"]},
        "order": [str(G1.id), "Réactions", str(G2.id), "Fêtes", str(G3.id), "Vide"], "updated_at": time.time()}})


async def main(port):
    seed()
    bot = S.bot
    bot.oauth_sessions[TOKEN] = {"user_id": "20", "username": "Alice", "avatar_url": None, "discord_access_token": "x", "discord_refresh_token": "y",
                                 "discord_token_expires_at": time.time() + 10 ** 7, "created_at": time.time(), "last_used_at": time.time()}

    async def memberships(user_id):
        return [(g, g.members[user_id]) for g in GUILDS if user_id in g.members]
    async def find_member_async(user_id):
        return {20: ME, 21: FRIEND}.get(user_id)
    async def current_channel(user_id):
        return G1.id, VC.channel
    async def join(guild_id, channel):
        bot.voice_clients_map[guild_id], bot.mixers[guild_id] = VC(), bot.mixers.get(guild_id) or A.MixingAudioSource()
        return bot.voice_clients_map[guild_id]
    bot.find_memberships, bot.find_member_async, bot.find_member = memberships, find_member_async, lambda uid: {20: ME, 21: FRIEND}.get(uid)
    bot.find_current_voice_channel, bot.join_guild_voice = current_channel, join
    bot.get_guild = lambda gid: next((g for g in GUILDS if g.id == gid), None)
    S.record_play_stat = lambda *a, **k: None

    class FakeFF(A.discord.AudioSource):
        def __init__(self, path, **kw): self.path = path
        def read(self): return A.SILENCE
        def cleanup(self): pass
    S.discord.FFmpegPCMAudio = lambda path, **kw: FakeFF(path, **kw)

    bot.voice_clients_map, bot.mixers = {G1.id: VC()}, {G1.id: A.MixingAudioSource()}
    S.SHARED_SECRET = "preview-secret-not-used-by-the-web-page"
    S.OAUTH2_CLIENT_ID, S.OAUTH2_CLIENT_SECRET = "123456789012345678", "x"
    S.PUBLIC_URL = f"http://localhost:{port}"
    S.HTTP_HOST, S.HTTP_PORT = "127.0.0.1", port
    bot.is_ready = lambda: True
    await bot._start_http_server()

    # Activité Discord simulée : sans Discord, le kit est remplacé par une imitation (même interface : ready, guildId, channelId,
    # commands.authorize / authenticate) et l'échange du code par un faux. Ouvrir http://localhost:PORT/app/?frame_id=1&instance_id=2
    MOCK_SDK = b"""export class DiscordSDK {
  constructor(clientId) { this.clientId = clientId; this.guildId = '9001'; this.channelId = '1'; this.calls = []; window.__sdkCalls = this.calls;
    this.commands = {
      authorize: async (args) => { this.calls.push(['authorize', args]); return { code: 'code-factice' }; },
      authenticate: async (args) => { this.calls.push(['authenticate', args]); return { access_token: args.access_token }; },
    }; }
  async ready() { await new Promise((resolve) => setTimeout(resolve, 400)); }
}
export class DiscordSDKMock {}
export const Events = {}; export const Platform = {};
"""
    if bot.web_app is not None:
        from webapp import Asset
        old = bot.web_app.assets["vendor/discord-embedded-app-sdk.js"]
        bot.web_app.assets["vendor/discord-embedded-app-sdk.js"] = Asset(MOCK_SDK, old.content_type, '"mock-sdk"')

    async def fake_token_request(payload):
        print("échange du code : redirect_uri présent =", "redirect_uri" in payload, flush=True)
        return {"access_token": "faux-jeton-discord", "refresh_token": "rt", "expires_in": 3600}
    async def fake_identity(session):
        session["user_id"], session["username"], session["avatar_url"] = "20", "Alice", "https://cdn.discordapp.com/embed/avatars/0.png"
    bot._discord_token_request, bot._refresh_identity_from_discord = fake_token_request, fake_identity
    print(f"Aperçu prêt : http://localhost:{port}/app/   (page web activée : {bot.web_app is not None and bot.web_app.enabled})", flush=True)

    async def drain():                       # fait « jouer » les sons : sans lecteur vocal réel, on lit nous-mêmes le mixeur
        while True:
            for mixer in list(bot.mixers.values()):
                mixer.read()
            await asyncio.sleep(0.02)
    await drain()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.port))
    except KeyboardInterrupt:
        sys.exit(0)
