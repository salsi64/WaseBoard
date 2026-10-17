"""Messages d'erreur du serveur dans la langue du client.

Le client WaseBoard envoie la langue de son interface (`Accept-Language: fr` ou `en`) ; un appelant sans cet en-tête (ancien client,
script, page web) garde les messages français, comme avant. Le code du serveur continue d'écrire ses messages en français : ce module
traduit le texte au dernier moment (voir `language_middleware` dans server.py), à partir du catalogue CATALOG ci-dessous.

Une entrée est une paire (texte français, texte anglais). Un trou `{}` du texte français correspond à une valeur variable du message
(nombre, nom…) ; le texte anglais la reprend avec `{0}`, `{1}`… dans l'ordre. `tests/test_messages.py` lit server.py et effects.py et vérifie
que chaque message d'erreur écrit dans le code a son entrée ici : ajouter un message au serveur sans le traduire fait échouer les tests.
"""
import re
from typing import Optional

DEFAULT_LANGUAGE = "fr"
SUPPORTED = ("fr", "en")

# (français, anglais) — l'ordre suit celui de server.py.
CATALOG: list[tuple[str, str]] = [
    ("Trop de requêtes : réessayez dans {} s.", "Too many requests: try again in {0} s."),
    ("Trop de sons envoyés : réessayez dans {} s.", "Too many sounds sent: try again in {0} s."),
    ("Trop de sons en même temps sur ce serveur (maximum {}) : réessayez dans un instant.",
     "Too many sounds at the same time on this server (maximum {0}): try again in a moment."),
    ("Le serveur WaseBoard est très sollicité en ce moment : réessayez dans un instant.",
     "The WaseBoard server is very busy right now: try again in a moment."),
    ("son introuvable", "sound not found"),
    ("son introuvable : {}", "sound not found: {0}"),
    ("son introuvable dans la corbeille", "sound not found in the trash"),
    ("fichier manquant sur le serveur", "file missing on the server"),
    ("champs 'name', 'file' et 'guild_id' requis", "'name', 'file' and 'guild_id' fields are required"),
    ("extension non supportée : {}", "unsupported extension: {0}"),
    ("Fichier trop volumineux (maximum {} Mo sur ce serveur).", "File too large (maximum {0} MB on this server)."),
    ("Espace insuffisant sur ce serveur : {} Mo utilisés sur {} Mo, et ce fichier fait {} Mo.",
     "Not enough space on this server: {0} MB used out of {1} MB, and this file is {2} MB."),
    ("Espace insuffisant sur ce serveur : {} Mo utilisés sur {} Mo, et ce fichier fait {} Mo (l'ancien fichier de {} Mo est libéré par le remplacement).",
     "Not enough space on this server: {0} MB used out of {1} MB, and this file is {2} MB (the old {3} MB file is freed by the replacement)."),
    ("Son trop long ({} s, maximum {} s sur ce serveur).", "Sound too long ({0} s, maximum {1} s on this server)."),
    ("Seuls l'auteur du son ou un administrateur du serveur peuvent le modifier.", "Only the sound's author or a server administrator can edit it."),
    ("Seuls l'auteur du son ou un administrateur du serveur peuvent remplacer son fichier.",
     "Only the sound's author or a server administrator can replace its file."),
    ("Seuls l'auteur du son ou un administrateur du serveur peuvent le supprimer.", "Only the sound's author or a server administrator can delete it."),
    ("Seuls l'auteur du son ou un administrateur de ce serveur peuvent modifier sa catégorie partagée.",
     "Only the sound's author or an administrator of this server can change its shared category."),
    ("'name', 'emoji' ou une découpe requis", "'name', 'emoji' or a trim is required"),
    ("'name' ne peut pas etre vide", "'name' cannot be empty"),
    ("champ 'file' requis", "'file' field is required"),
    ("Vous n'avez pas le droit d'ajouter des sons sur ce serveur.", "You are not allowed to add sounds on this server."),
    ("Les ajouts de sons sont réservés aux administrateurs de ce serveur.", "Adding sounds is restricted to administrators of this server."),
    ("Un administrateur de ce serveur vous a retiré le droit d'ajouter des sons.", "An administrator of this server has removed your right to add sounds."),
    ("identifiant client OAuth2 indisponible côté serveur", "OAuth2 client ID unavailable on the server"),
    ("OAuth2 non configuré côté serveur", "OAuth2 not configured on the server"),
    ("corps JSON invalide", "invalid JSON body"),
    ("'code' et 'redirect_uri' requis", "'code' and 'redirect_uri' are required"),
    ("redirect_uri non autorisé", "redirect_uri not allowed"),
    ("Échange avec Discord échoué : {}", "Exchange with Discord failed: {0}"),
    ("Identité Discord introuvable : {}", "Discord identity not found: {0}"),
    ("guild_id requis", "guild_id is required"),
    ("action invalide", "invalid action"),
    ("vous n'êtes pas membre de ce serveur Discord", "you are not a member of this Discord server"),
    ("catégories invalides", "invalid categories"),
    ("trop de catégories ou de sons dans une catégorie", "too many categories, or too many sounds in a category"),
    ("Réservé aux administrateurs de ce serveur.", "Restricted to administrators of this server."),
    ("rôle introuvable sur ce serveur", "role not found on this server"),
    ("'upload_admins_only' doit être un booléen", "'upload_admins_only' must be a boolean"),
    ("'{}' doit être un entier entre 0 et {}", "'{0}' must be a whole number between 0 and {1}"),
    ("'{}' est limité à {} par l'hébergeur de ce serveur WaseBoard (« illimité » n'est pas permis).",
     "'{0}' is limited to {1} by the host of this WaseBoard server (“unlimited” is not allowed)."),
    ("'days' doit être un entier", "'days' must be a whole number"),
    ("paramètres invalides", "invalid parameters"),
    ("'user_id' (numérique) et 'blocked' (booléen) requis", "'user_id' (numeric) and 'blocked' (boolean) are required"),
    ("Impossible de bloquer un administrateur.", "Can't block an administrator."),
    ("Impossible de déterminer le salon Discord cible : rejoignez un salon vocal où le bot est présent (/join), puis réessayez.",
     "Can't determine the target Discord channel: join a voice channel where the bot is present (/join), then try again."),
    ("Impossible de déterminer le salon Discord cible.", "Can't determine the target Discord channel."),
    ("Le bot n'est connecté à aucun salon vocal sur ce serveur (utilisez /join).", "The bot is not connected to any voice channel on this server (use /join)."),
    ("Le bot n'est connecté à aucun salon vocal sur ce serveur.", "The bot is not connected to any voice channel on this server."),
    ("Vous n'êtes pas membre du serveur Discord ciblé.", "You are not a member of the targeted Discord server."),
    ("Vous n'êtes dans aucun salon vocal sur les serveurs Discord où je suis présent.", "You are not in any voice channel on the Discord servers where I am present."),
    ("Ce son n'appartient à aucune de vos guildes Discord connues.", "This sound doesn't belong to any of your known Discord servers."),
    ("Les boucles sont désactivées sur ce serveur.", "Loops are disabled on this server."),
    ("Ce son est trop long (ou illisible) pour tourner en boucle.", "This sound is too long (or unreadable) to loop."),
    ("« steps » doit contenir de 1 à {} sons.", "“steps” must contain 1 to {0} sounds."),
    ("Étape {} : un objet est attendu.", "Step {0}: an object is expected."),
    ("Étape {} : {}", "Step {0}: {1}"),
    ("Séquence trop longue (plus de {} s avant la dernière étape).", "Sequence too long (more than {0} s before the last step)."),
    ("« delay_ms » doit être un nombre de 0 à {}.", "“delay_ms” must be a number from 0 to {0}."),
    ("« volume » doit être un nombre.", "“volume” must be a number."),
    # Découpe
    ("La découpe demande un début ET une fin.", "A trim needs both a start AND an end."),
    ("Le début et la fin de la découpe doivent être des nombres de millisecondes.", "The start and end of the trim must be numbers of milliseconds."),
    ("Découpe invalide : la portion gardée doit durer au moins 0,1 s.", "Invalid trim: the kept portion must last at least 0.1 s."),
    # Effets (effects.py)
    ("Effet « {} » : un nombre est attendu.", "Effect “{0}”: a number is expected."),
    ("Effet « {} » : la valeur doit être comprise entre {} et {}.", "Effect “{0}”: the value must be between {1} and {2}."),
    ("« effects » doit être un objet, par exemple {\"pitch\": 3, \"echo\": true}.", "“effects” must be an object, for example {\"pitch\": 3, \"echo\": true}."),
    ("Effet inconnu : « {} » (possibles : {}).", "Unknown effect: “{0}” (possible: {1})."),
]

#: Textes montrés par le bot dans Discord (réponses des commandes et des boutons, descriptions des commandes) : traduits selon la langue de Discord
#: de la personne (`interaction.locale`). Les panneaux et commandes d'administration (/bot-setup, /diagnostic…) restent en français.
DISCORD_CATALOG: list[tuple[str, str]] = [
    # Réponses
    ("Vous devez d'abord être connecté à un salon vocal.", "You need to be connected to a voice channel first."),
    ("Connectez-vous d'abord à un salon vocal sur Discord, puis recliquez ce bouton.", "Join a voice channel on Discord first, then click this button again."),
    ("Connecté à **{}**. Prêt à jouer les sons envoyés par WaseBoard.", "Connected to **{0}**. Ready to play the sounds sent by WaseBoard."),
    ("Déconnecté du salon vocal.", "Disconnected from the voice channel."),
    ("Je ne suis connecté à aucun salon vocal ici.", "I'm not connected to any voice channel here."),
    ("Ce serveur WaseBoard est saturé ({} salons vocaux actifs en même temps) : réessayez dans quelques minutes.",
     "This WaseBoard server is at capacity ({0} active voice channels at the same time): try again in a few minutes."),
    ("Plusieurs sons correspondent à « {} » : choisissez-en un dans la liste proposée.", "Several sounds match “{0}”: pick one from the suggested list."),
    ("Aucun son ne correspond à « {} ».", "No sound matches “{0}”."),
    ("❌ Le fichier de ce son est introuvable sur le serveur.", "❌ This sound's file is missing on the server."),
    ("Le bot est dans « {} ». Rejoignez ce salon, ou utilisez /join pour l'amener dans le vôtre.",
     "The bot is in “{0}”. Join that channel, or use /join to bring it to yours."),
    ("❌ Impossible de rejoindre votre salon vocal (le bot a-t-il la permission « Se connecter » ?).",
     "❌ Can't join your voice channel (does the bot have the “Connect” permission?)."),
    ("⏹️ Sons coupés.", "⏹️ Sounds stopped."),
    ("⏹️ {} coupé.", "⏹️ {0} stopped."),
    ("{} ne jouait pas.", "{0} wasn't playing."),
    ("❌ Impossible de calculer le classement (voir les journaux du serveur).", "❌ Couldn't compute the ranking (see the server logs)."),
    ("❌ Impossible de calculer le bilan (voir les journaux du serveur).", "❌ Couldn't compute the review (see the server logs)."),
    ("Lien non configuré côté serveur (public_url/shared_secret manquants dans config.json).",
     "Link not configured on the server (public_url/shared_secret missing from config.json)."),
    ("Cliquez sur le bouton : WaseBoard s'ouvre et se connecte directement à CE serveur (utile si votre application est reliée à une autre instance — le lien expire dans {} minutes).",
     "Click the button: WaseBoard opens and connects directly to THIS server (useful if your app is linked to another instance — the link expires in {0} minutes)."),
    ("Ajoutez ce bot WaseBoard à un **autre** serveur Discord (le vôtre ou celui d'un ami) — chaque serveur obtient sa **propre bibliothèque de sons**, totalement indépendante des autres : pratique si vous gérez plusieurs communautés Discord. Il faut avoir le droit « Gérer le serveur » sur ce nouveau serveur pour l'ajouter.",
     "Add this WaseBoard bot to **another** Discord server (yours or a friend's) — each server gets its **own sound library**, completely independent of the others: handy if you run several Discord communities. You need the “Manage Server” permission on that new server to add it."),
    ("**Comment utiliser WaseBoard**\n1. Rejoignez un salon vocal sur Discord.\n2. Cliquez **🔊 Rejoindre mon vocal** (ici ou dans l'application) : le bot vous rejoint.\n3. Dans l'application WaseBoard, cliquez un son : il joue chez tout le monde dans ce salon.\n\nRien ne s'entend ? Vérifiez que le bot est bien dans **votre** salon (bouton 🔊 ci-dessus). Pas encore installé WaseBoard ? Cliquez le bouton 📬 pour recevoir votre lien de connexion.\nGuide complet : demandez le lien à un administrateur de ce serveur.",
     "**How to use WaseBoard**\n1. Join a voice channel on Discord.\n2. Click **🔊 Join my voice channel** (here or in the app): the bot joins you.\n3. In the WaseBoard app, click a sound: it plays for everyone in that channel.\n\nCan't hear anything? Check that the bot is in **your** channel (🔊 button above). Haven't installed WaseBoard yet? Click the 📬 button to get your connection link.\nFull guide: ask a server administrator for the link."),
    # Descriptions des commandes, de leurs options et de leurs choix (menu des commandes de Discord)
    ("Fait rejoindre le bot à votre salon vocal actuel", "Makes the bot join your current voice channel"),
    ("Déconnecte le bot du salon vocal", "Disconnects the bot from the voice channel"),
    ("Poste un panneau expliquant comment démarrer avec WaseBoard (téléchargement, connexion)", "Posts a panel explaining how to get started with WaseBoard (download, sign-in)"),
    ("Poste un bouton pour ajouter ce bot WaseBoard à un autre serveur Discord", "Posts a button to add this WaseBoard bot to another Discord server"),
    ("Poste un panneau de boutons (rejoindre le vocal, aide) utilisable par tous les membres", "Posts a button panel (join voice, help) that every member can use"),
    ("Poste un panneau tout-en-un (démarrer, actions, inviter ailleurs, liens) — une seule commande", "Posts an all-in-one panel (get started, actions, invite elsewhere, links) — a single command"),
    ("Vérifie la configuration de WaseBoard sur ce serveur (réservé aux administrateurs)", "Checks WaseBoard's configuration on this server (administrators only)"),
    ("Santé et capacité de cette instance WaseBoard (propriétaire de l'application Discord)", "Health and capacity of this WaseBoard instance (Discord application owner)"),
    ("Joue un son dans votre salon vocal, sans ouvrir l'application", "Plays a sound in your voice channel, without opening the app"),
    ("Le son à jouer (tapez quelques lettres pour chercher)", "The sound to play (type a few letters to search)"),
    ("Coupe les sons en cours dans le salon vocal", "Stops the sounds playing in the voice channel"),
    ("Couper seulement ce son (une boucle, par exemple) ; sans lui, tous les sons sont coupés", "Stop only this sound (a loop, for example); without it, all sounds are stopped"),
    ("Les sons les plus joués et les membres les plus actifs du serveur", "The most played sounds and the most active members of the server"),
    ("Période étudiée (30 jours par défaut)", "Period to look at (30 days by default)"),
    ("Poster le classement dans le salon (sinon, vous seul le voyez)", "Post the ranking in the channel (otherwise only you see it)"),
    ("7 derniers jours", "Last 7 days"),
    ("30 derniers jours", "Last 30 days"),
    ("90 derniers jours", "Last 90 days"),
    ("12 derniers mois", "Last 12 months"),
    ("Le bilan de l'année : sons les plus joués, membres les plus actifs, vos chiffres", "The year in review: most played sounds, most active members, your numbers"),
    ("Année du bilan (l'année en cours par défaut)", "Year to review (the current year by default)"),
    ("Poster le bilan dans le salon (sinon, vous seul le voyez)", "Post the review in the channel (otherwise only you see it)"),
    # Génériques EN DERNIER : le premier motif qui correspond l'emporte, et le trou est lui-même traduit (message de capacité, de lecture…).
    ("⚠️ {}", "⚠️ {0}"),
    ("❌ {}", "❌ {0}"),
]

#: Noms de commandes et d'options affichés en anglais dans le menu de Discord (le code garde les noms français : seuls les noms localisés changent).
COMMAND_NAMES_EN = {"configurer-invitation": "setup-invite", "inviter-bot": "invite-bot", "panneau": "panel", "bilan": "recap"}
PARAMETER_NAMES_EN = {"son": "sound", "periode": "period", "annee": "year", "partager": "share"}

_HOLE = "{}"


def _compile(french: str) -> "re.Pattern[str]":
    parts = french.split(_HOLE)
    return re.compile("(.+?)".join(re.escape(part) for part in parts), re.S)


_COMPILED = [(_compile(fr), en) for fr, en in CATALOG + DISCORD_CATALOG]
_TOKEN = re.compile(r"\{(\d+)\}")


def pick_language(accept_language: Optional[str]) -> str:
    """Langue demandée par l'en-tête Accept-Language (la première langue gérée, par ordre de préférence) ; français par défaut."""
    if not accept_language:
        return DEFAULT_LANGUAGE
    candidates = []
    for position, item in enumerate(accept_language.split(",")):
        tag, _, params = item.strip().partition(";")
        quality = 1.0
        match = re.search(r"q\s*=\s*([0-9.]+)", params)
        if match:
            try:
                quality = float(match.group(1))
            except ValueError:
                quality = 0.0
        code = tag.strip().lower().split("-")[0]
        if code and quality > 0:
            candidates.append((-quality, position, code))
    for _, _, code in sorted(candidates):
        if code in SUPPORTED:
            return code
    return DEFAULT_LANGUAGE


def discord_language(locale) -> str:
    """Langue des réponses du bot dans Discord, d'après la langue de Discord de la personne (`interaction.locale`, ex. « fr », « en-US ») :
    français pour le français, anglais pour toutes les autres langues (le français n'est pas une valeur par défaut pour le monde entier)."""
    code = str(getattr(locale, "value", locale) or "").lower().split("-")[0].split("_")[0]
    return "fr" if code in ("", "fr") else "en"


def translate(text: str, language: str) -> str:
    """Le message dans la langue demandée ; inchangé s'il est déjà dans cette langue, ou inconnu du catalogue."""
    if language == DEFAULT_LANGUAGE or language not in SUPPORTED or not isinstance(text, str):
        return text
    for pattern, english in _COMPILED:
        match = pattern.fullmatch(text)
        if match:
            # Un trou peut lui-même être un message du catalogue (« Étape 2 : <message de validation> »).
            groups = [translate(group, language) for group in match.groups()]
            return _TOKEN.sub(lambda token: groups[int(token.group(1))], english)
    return text


def is_translated(french_text: str) -> bool:
    """Vrai si ce texte français (trous `{}` compris) figure au catalogue — pour les tests."""
    return any(fr == french_text for fr, _ in CATALOG + DISCORD_CATALOG)
