"""Panneau de boutons Discord (/panneau) : rejoindre le vocal, stop, aide. Vrai code de server.py, fausses
Lancé via tests/run_all.sh (dossier de données isolé et jetable, voir _env.py)."""
import asyncio
import sys
import time
from types import SimpleNamespace

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import discord  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}   {name}" + (f"   [{detail}]" if detail and not cond else ""))


bot = S.bot


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))


class FakeInteraction:
    def __init__(self, user, guild_id=4242):
        self.user, self.guild_id = user, guild_id
        self.response = FakeResponse()
        self.channel = SimpleNamespace(send=self._channel_send)
        self.channel_sent = []

    async def _channel_send(self, content=None, **kw):
        self.channel_sent.append((content, kw))


class FakeMember(discord.Member):
    def __init__(self, voice_channel=None):
        self._voice_channel = voice_channel

    @property
    def voice(self):
        return SimpleNamespace(channel=self._voice_channel) if self._voice_channel is not None else None


view = S.MemberPanelView()
join_btn, stop_btn, help_btn = view.children


async def main():
    print("--- bouton 🔊 Rejoindre mon vocal ---")
    inter = FakeInteraction(SimpleNamespace(id=1))  # ni discord.Member ni voice : objet quelconque
    await join_btn.callback(inter)
    text, kw = inter.response.sent[0]
    check("utilisateur hors vocal (pas un Member) : message éphémère, pas d'appel au bot",
          "Connectez-vous d'abord" in text and kw.get("ephemeral") is True)

    inter = FakeInteraction(FakeMember(voice_channel=None))
    await join_btn.callback(inter)
    check("Member sans salon vocal : même message éphémère", "Connectez-vous d'abord" in inter.response.sent[0][0])

    channel = SimpleNamespace(name="Général")
    calls = []

    async def fake_join(guild_id, ch):
        calls.append((guild_id, ch))
        return "vc"

    real_join = bot.join_guild_voice
    bot.join_guild_voice = fake_join
    try:
        inter = FakeInteraction(FakeMember(voice_channel=channel), guild_id=999)
        await join_btn.callback(inter)
        text, kw = inter.response.sent[0]
        check("en vocal : le bot est appelé avec la bonne guilde/le bon salon",
              calls == [(999, channel)], calls)
        check("message de confirmation avec le nom du salon, éphémère",
              "Général" in text and "Prêt à jouer" in text and kw.get("ephemeral") is True, text)

        async def fake_join_full(guild_id, ch):
            raise S.CapacityError("Ce serveur WaseBoard est saturé (2 salons vocaux actifs en même temps).")

        bot.join_guild_voice = fake_join_full
        inter = FakeInteraction(FakeMember(voice_channel=channel))
        await join_btn.callback(inter)
        text, kw = inter.response.sent[0]
        check("instance saturée : message ⚠️ éphémère au lieu d'un plantage",
              "⚠️" in text and "saturé" in text and kw.get("ephemeral") is True, text)
    finally:
        bot.join_guild_voice = real_join

    print("\n--- bouton ⏹️ Stop ---")
    bot.mixers = {}
    inter = FakeInteraction(FakeMember(), guild_id=111)
    await stop_btn.callback(inter)
    check("aucun mixeur pour cette guilde : message informatif, pas d'erreur",
          "Aucun son" in inter.response.sent[0][0] and inter.response.sent[0][1].get("ephemeral") is True)

    class FakeMixer:
        def __init__(self, n):
            self._n, self.cleared = n, False

        def count(self):
            return self._n

        def clear(self):
            self.cleared = True
            self._n = 0

    empty_mixer = FakeMixer(0)
    bot.mixers = {111: empty_mixer}
    inter = FakeInteraction(FakeMember(), guild_id=111)
    await stop_btn.callback(inter)
    check("mixeur présent mais vide (count=0) : même message, clear() pas nécessaire",
          "Aucun son" in inter.response.sent[0][0] and not empty_mixer.cleared)

    busy_mixer = FakeMixer(3)
    bot.mixers = {111: busy_mixer, 222: FakeMixer(5)}
    inter = FakeInteraction(FakeMember(), guild_id=111)
    await stop_btn.callback(inter)
    text, kw = inter.response.sent[0]
    check("sons en cours : clear() appelé sur LE MIXEUR DE CETTE GUILDE SEULEMENT",
          busy_mixer.cleared and not bot.mixers[222].cleared, (busy_mixer.cleared, bot.mixers[222].cleared))
    check("message de confirmation, éphémère", "coupés" in text and kw.get("ephemeral") is True, text)

    print("\n--- bouton ❓ Aide ---")
    inter = FakeInteraction(FakeMember())
    await help_btn.callback(inter)
    text, kw = inter.response.sent[0]
    check("texte d'aide envoyé en message éphémère", kw.get("ephemeral") is True)
    check("mentionne les 3 étapes essentielles (vocal, bouton 🔊, cliquer un son)",
          all(w in text for w in ("salon vocal", "Rejoindre mon vocal", "cliquez un son")), text)
    check("sous la limite Discord de 2000 caractères", len(text) <= 2000, len(text))

    print("\n--- commande /panneau ---")
    class FakeGuildPerms:
        def __init__(self, admin):
            self.administrator = admin

    class FakeDiscordMember(discord.Member):
        voice = None

        def __init__(self, admin):
            self._admin = admin

        @property
        def guild_permissions(self):
            return FakeGuildPerms(self._admin)

    inter = FakeInteraction(FakeDiscordMember(False))
    await S.panneau.callback(inter)
    check("non-admin : refus poli, éphémère, rien posté dans le salon",
          not inter.channel_sent and inter.response.sent and inter.response.sent[0][1].get("ephemeral") is True
          and "administrateurs" in inter.response.sent[0][0], (inter.channel_sent, inter.response.sent))

    inter = FakeInteraction(FakeDiscordMember(True))
    await S.panneau.callback(inter)
    check("admin : poste un message dans le salon avec le MemberPanelView, puis confirme en éphémère",
          len(inter.channel_sent) == 1 and isinstance(inter.channel_sent[0][1].get("view"), S.MemberPanelView)
          and inter.response.sent[0][1].get("ephemeral") is True and "posté" in inter.response.sent[0][0].lower(),
          (inter.channel_sent, inter.response.sent))
    check("masquée aux non-admins dans l'interface Discord ET réservée aux serveurs (défense en profondeur, comme /diagnostic)",
          S.panneau.default_permissions is not None and S.panneau.default_permissions.administrator and S.panneau.guild_only)

    print("\n--- commande /inviter-bot ---")
    inter = FakeInteraction(FakeDiscordMember(False))
    await S.inviter_bot.callback(inter)
    check("non-admin : refus poli, éphémère, rien posté dans le salon",
          not inter.channel_sent and inter.response.sent and inter.response.sent[0][1].get("ephemeral") is True
          and "administrateurs" in inter.response.sent[0][0], (inter.channel_sent, inter.response.sent))

    real_client_id = S.OAUTH2_CLIENT_ID
    S.OAUTH2_CLIENT_ID = "999999"
    try:
        inter = FakeInteraction(FakeDiscordMember(True))
        await S.inviter_bot.callback(inter)
        check("admin : poste un message dans le salon avec un bouton-lien, puis confirme en éphémère",
              len(inter.channel_sent) == 1 and inter.response.sent[0][1].get("ephemeral") is True
              and "posté" in inter.response.sent[0][0].lower(), (inter.channel_sent, inter.response.sent))
        link_view = inter.channel_sent[0][1].get("view")
        buttons = list(link_view.children) if link_view else []
        check("un seul bouton, de style lien", len(buttons) == 1 and buttons[0].style.name == "link", buttons)
        url = buttons[0].url if buttons else ""
        check("l'URL pointe vers le bon client_id, avec les permissions/scope attendus (diagnostics.build_invite_url)",
              url == S.diagnostics.build_invite_url("999999"), url)
    finally:
        S.OAUTH2_CLIENT_ID = real_client_id

    check("masquée aux non-admins dans l'interface Discord ET réservée aux serveurs (défense en profondeur, comme /panneau)",
          S.inviter_bot.default_permissions is not None and S.inviter_bot.default_permissions.administrator
          and S.inviter_bot.guild_only)

    print("\n--- vue persistante ---")
    check("timeout=None (persistante : survit aux redémarrages du bot)", S.MemberPanelView().timeout is None)
    custom_ids = {b.custom_id for b in S.MemberPanelView().children}
    check("3 boutons, avec des custom_id fixes et distincts de ceux du panneau d'invitation",
          custom_ids == {"waseboard_panel_join", "waseboard_panel_stop", "waseboard_panel_help"}
          and not custom_ids & {"waseboard_invite_button"}, custom_ids)

    print()
    print(f"{sum(results)}/{len(results)} vérifications OK")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
