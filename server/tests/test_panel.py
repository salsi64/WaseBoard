"""Panneaux de boutons Discord (/panneau, /inviter-bot, /bot-setup) : rejoindre le vocal, aide,
inviter ailleurs, panneau tout-en-un. Vrai code de server.py, fausses interactions Discord.
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
join_btn, help_btn = view.children


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

    print("\n--- commande /configurer-invitation ---")
    S.PUBLIC_URL, S.SHARED_SECRET = "https://exemple.test", "s3cret"
    inter = FakeInteraction(FakeDiscordMember(True))
    await S.configurer_invitation.callback(inter)
    check("poste UN panneau InviteView expliquant téléchargement + connexion Discord + cas auto-hébergé, "
          "puis confirme en éphémère",
          len(inter.channel_sent) == 1 and isinstance(inter.channel_sent[0][1].get("view"), S.InviteView)
          and "Téléchargez" in inter.channel_sent[0][0] and "Discord" in inter.channel_sent[0][0]
          and "propre instance" in inter.channel_sent[0][0]
          and inter.response.sent[0][1].get("ephemeral") is True and "posté" in inter.response.sent[0][0].lower(),
          (inter.channel_sent, inter.response.sent))

    S.PUBLIC_URL, S.SHARED_SECRET = "", ""
    inter = FakeInteraction(FakeDiscordMember(True))
    await S.configurer_invitation.callback(inter)
    check("sans public_url/shared_secret : avertissement, rien posté dans le salon",
          not inter.channel_sent and "public_url" in inter.response.sent[0][0]
          and inter.response.sent[0][1].get("ephemeral") is True, (inter.channel_sent, inter.response.sent))
    S.PUBLIC_URL, S.SHARED_SECRET = "https://exemple.test", "s3cret"

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
        check("le message explique la bibliothèque de sons indépendante par serveur",
              "bibliothèque de sons" in inter.channel_sent[0][0], inter.channel_sent[0][0])
    finally:
        S.OAUTH2_CLIENT_ID = real_client_id

    check("masquée aux non-admins dans l'interface Discord ET réservée aux serveurs (défense en profondeur, comme /panneau)",
          S.inviter_bot.default_permissions is not None and S.inviter_bot.default_permissions.administrator
          and S.inviter_bot.guild_only)

    print("\n--- vue persistante : /panneau (2 boutons depuis le retrait de Stop) ---")
    check("timeout=None (persistante : survit aux redémarrages du bot)", S.MemberPanelView().timeout is None)
    custom_ids = {b.custom_id for b in S.MemberPanelView().children}
    check("2 boutons (Stop retiré), custom_id fixes et distincts de ceux du panneau d'invitation",
          custom_ids == {"waseboard_panel_join", "waseboard_panel_help"}
          and not custom_ids & {"waseboard_invite_button"}, custom_ids)

    print("\n--- BotSetupView (/bot-setup) ---")
    setup_view = S.BotSetupView()
    check("timeout=None (persistante)", setup_view.timeout is None)
    all_children = list(setup_view.children)
    check("8 éléments : 4 boutons-liens + 4 boutons d'action", len(all_children) == 8, len(all_children))
    link_buttons = {b.label: b.url for b in all_children if b.style.name == "link"}
    check("boutons-liens statiques : télécharger, site, Discord, GitHub",
          set(link_buttons) == {"⬇ Télécharger WaseBoard", "🌐 Site", "💬 Discord WaseBoard", "📦 GitHub"}, link_buttons)
    check("aucun bouton-lien n'a de custom_id (interdit par Discord)",
          all(b.custom_id is None for b in all_children if b.style.name == "link"))
    action_custom_ids = {b.custom_id for b in all_children if b.style.name != "link"}
    check("4 boutons d'action, custom_id fixes et distincts de tous les autres panneaux",
          action_custom_ids == {"waseboard_setup_connect", "waseboard_setup_join", "waseboard_setup_help", "waseboard_setup_invite"}
          and not action_custom_ids & (custom_ids | {"waseboard_invite_button"}), action_custom_ids)

    setup_connect_btn, setup_join_btn, setup_help_btn, setup_invite_btn = (
        b for b in all_children if b.style.name != "link")

    S.PUBLIC_URL, S.SHARED_SECRET = "https://exemple.test", "s3cret"
    inter = FakeInteraction(FakeMember())
    await setup_connect_btn.callback(inter)
    text, kw = inter.response.sent[0]
    connect_view = kw.get("view")
    check("bouton Connexion directe : réponse éphémère avec un bouton-lien vers /connect/<code>",
          kw.get("ephemeral") is True and connect_view is not None
          and len(list(connect_view.children)) == 1 and "/connect/" in list(connect_view.children)[0].url, (text, kw))

    inter = FakeInteraction(FakeMember())
    await setup_help_btn.callback(inter)
    check("bouton Aide : même texte que /panneau (PANEL_HELP_TEXT)",
          inter.response.sent[0][0] == S.PANEL_HELP_TEXT and inter.response.sent[0][1].get("ephemeral") is True)

    real_client_id2 = S.OAUTH2_CLIENT_ID
    S.OAUTH2_CLIENT_ID = "999999"
    try:
        inter = FakeInteraction(FakeMember())
        await setup_invite_btn.callback(inter)
        text, kw = inter.response.sent[0]
        buttons = list(kw.get("view").children) if kw.get("view") else []
        check("bouton Ajouter à un autre serveur : même logique que /inviter-bot, réponse éphémère",
              kw.get("ephemeral") is True and len(buttons) == 1
              and buttons[0].url == S.diagnostics.build_invite_url("999999")
              and "bibliothèque de sons" in text, (text, kw))
    finally:
        S.OAUTH2_CLIENT_ID = real_client_id2

    print("\n--- commande /bot-setup ---")
    inter = FakeInteraction(FakeDiscordMember(False))
    await S.bot_setup.callback(inter)
    check("non-admin : refus poli, éphémère, rien posté dans le salon",
          not inter.channel_sent and "administrateurs" in inter.response.sent[0][0]
          and inter.response.sent[0][1].get("ephemeral") is True, (inter.channel_sent, inter.response.sent))

    inter = FakeInteraction(FakeDiscordMember(True))
    await S.bot_setup.callback(inter)
    check("admin : poste UN panneau BotSetupView, puis confirme en éphémère",
          len(inter.channel_sent) == 1 and isinstance(inter.channel_sent[0][1].get("view"), S.BotSetupView)
          and inter.response.sent[0][1].get("ephemeral") is True and "posté" in inter.response.sent[0][0].lower(),
          (inter.channel_sent, inter.response.sent))
    check("masquée aux non-admins dans l'interface Discord ET réservée aux serveurs",
          S.bot_setup.default_permissions is not None and S.bot_setup.default_permissions.administrator
          and S.bot_setup.guild_only)

    embed = inter.channel_sent[0][1].get("embed")
    check("un embed coloré (pas du texte brut) porte le panneau",
          embed is not None and inter.channel_sent[0][0] is None and embed.color is not None
          and embed.color.value == 0x38BDF8, embed)
    field_names = [f.name for f in embed.fields] if embed else []
    check("4 champs, « autre serveur auto-hébergé » en DERNIER (précision secondaire, pas la première lue)",
          len(field_names) == 4 and "auto-hébergé" in field_names[-1] and "Démarrer" in field_names[0], field_names)

    print()
    print(f"{sum(results)}/{len(results)} vérifications OK")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
