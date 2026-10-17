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

_HOLE = "{}"


def _compile(french: str) -> "re.Pattern[str]":
    parts = french.split(_HOLE)
    return re.compile("(.+?)".join(re.escape(part) for part in parts), re.S)


_COMPILED = [(_compile(fr), en) for fr, en in CATALOG]
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
    return any(fr == french_text for fr, _ in CATALOG)
