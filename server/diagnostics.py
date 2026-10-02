"""Vérifications de configuration du serveur WaseBoard.

Utilisé de deux façons, avec exactement les mêmes contrôles :
- en ligne de commande, AVANT de démarrer le bot (`python server.py --check`, `--invite-url`) — uniquement des
  appels REST à Discord, jamais de connexion à la passerelle : sans risque de doublon avec une instance déjà
  lancée avec le même jeton ;
- depuis Discord, commande `/diagnostic` (administrateurs) : mêmes contrôles + ceux qui ont besoin du bot en
  direct (droits sur les salons vocaux du serveur).

Chaque contrôle produit un `Check` (✅ / ⚠️ / ❌ / ℹ️) avec, si besoin, une phrase qui dit quoi faire.
"""

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import aiohttp

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/salsi64/WaseBoard, 1.0)"
# Voir le salon (1<<10) + Se connecter (1<<20) + Parler (1<<21) : le strict nécessaire pour jouer des sons.
BOT_PERMISSIONS = (1 << 10) | (1 << 20) | (1 << 21)
OAUTH_REDIRECT = "http://127.0.0.1:48899/callback/"
# Drapeaux d'application Discord : intent « Server Members » activé (vérifié / en attente de vérification).
FLAG_GUILD_MEMBERS = 1 << 14
FLAG_GUILD_MEMBERS_LIMITED = 1 << 15

PLACEHOLDER_SECRETS = {"", "changez-moi", "changeme", "change-me", "secret"}
ICONS = {"ok": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️"}

PORTAL_URL = "https://discord.com/developers/applications"


@dataclass
class Check:
    status: str  # "ok" | "warn" | "fail" | "info"
    title: str
    detail: str = ""


def render(checks: list[Check], limit: int = 1900, markdown: bool = True) -> str:
    """Texte lisible : message Discord (gras en markdown, limité à 2000 caractères) ou terminal (markdown=False)."""
    bold = "**" if markdown else ""
    lines = [f"{ICONS[c.status]} {bold}{c.title}{bold}" + (f" — {c.detail}" if c.detail else "") for c in checks]
    counts = {s: sum(1 for c in checks if c.status == s) for s in ("ok", "warn", "fail")}
    summary = f"\n{counts['ok']} ✅ · {counts['warn']} ⚠️ · {counts['fail']} ❌"
    text = "\n".join(lines)
    if len(text) + len(summary) > limit:
        text = text[: limit - len(summary) - 2].rstrip() + " …"
    return text + summary


def has_failure(checks: list[Check]) -> bool:
    return any(c.status == "fail" for c in checks)


# ---------- Contrôles sans réseau ----------

def _is_local_host(host: str) -> bool:
    host = (host or "").lower()
    if host in ("localhost", "") or host.endswith(".local") or host.endswith(".lan"):
        return True
    if host.startswith(("127.", "10.", "192.168.", "169.254.")):
        return True
    if host.startswith("172."):
        try:
            return 16 <= int(host.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def check_config(config: dict) -> list[Check]:
    checks: list[Check] = []

    if not config.get("bot_token"):
        checks.append(Check("fail", "Jeton du bot", "absent : renseignez bot_token (config.json) ou WASEBOARD_BOT_TOKEN. "
                                                    f"Portail Discord > votre application > Bot > Reset Token ({PORTAL_URL})."))

    secret = config.get("shared_secret") or ""
    if secret.strip().lower() in PLACEHOLDER_SECRETS:
        checks.append(Check("fail", "Secret partagé", "vide ou valeur d'exemple : l'API serait ouverte à tous. "
                                                      "Choisissez une longue valeur aléatoire (32 caractères ou plus)."))
    elif len(secret) < 32:
        checks.append(Check("warn", "Secret partagé", f"seulement {len(secret)} caractères : préférez 32 ou plus "
                                                      "(il protège toute l'API)."))
    else:
        checks.append(Check("ok", "Secret partagé", "défini et assez long"))

    url = (config.get("public_url") or "").strip()
    if not url:
        checks.append(Check("warn", "Adresse publique (public_url)", "non renseignée : le bouton d'invitation Discord "
                                                                     "ne pourra pas produire de lien."))
    else:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            checks.append(Check("fail", "Adresse publique (public_url)", f"« {url} » n'est pas une adresse valide "
                                                                         "(ex : https://waseboard.exemple.com)."))
        elif parsed.scheme == "https":
            checks.append(Check("ok", "Adresse publique (public_url)", url))
        elif _is_local_host(parsed.hostname):
            checks.append(Check("ok", "Adresse publique (public_url)", f"{url} (réseau local seulement, sans HTTPS)"))
        else:
            checks.append(Check("warn", "Adresse publique (public_url)", f"{url} n'est pas chiffrée : le secret partagé "
                                                                         "circulerait en clair sur Internet. Utilisez https://."))

    if not config.get("oauth2_client_secret"):
        checks.append(Check("fail", "Connexion Discord (OAuth2)", "secret client absent : personne ne pourra se connecter. "
                                                                  "Portail > OAuth2 > Reset Secret, puis oauth2_client_secret."))
    else:
        checks.append(Check("ok", "Connexion Discord (OAuth2)", "secret client renseigné"))
    return checks


def check_tools() -> list[Check]:
    checks: list[Check] = []
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path:
            checks.append(Check("ok", tool, path))
        elif tool == "ffmpeg":
            checks.append(Check("fail", "ffmpeg", "introuvable : sans lui, aucun son ne peut être joué (apt install ffmpeg)."))
        else:
            checks.append(Check("warn", "ffprobe", "introuvable (livré avec ffmpeg) : la durée des sons ne pourra pas être vérifiée."))

    try:
        import discord.opus as opus
        loaded = opus.is_loaded() or opus._load_default()
    except Exception:
        loaded = False
    checks.append(Check("ok", "Opus (encodage vocal)", "chargé") if loaded else
                  Check("fail", "Opus (encodage vocal)", "bibliothèque libopus introuvable (apt install libopus0)."))

    try:
        import nacl  # noqa: F401
        checks.append(Check("ok", "PyNaCl (chiffrement vocal)", "présent"))
    except ImportError:
        checks.append(Check("fail", "PyNaCl (chiffrement vocal)", "absent (pip install -r requirements.txt)."))
    return checks


def check_data_dir(path: Path) -> list[Check]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".waseboard-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as ex:
        return [Check("fail", "Dossier de données", f"{path} n'est pas inscriptible ({ex}). Vérifiez les droits du volume.")]
    free = shutil.disk_usage(path).free
    gib = free / (1024 ** 3)
    if free < 200 * 1024 * 1024:
        return [Check("fail", "Dossier de données", f"{path} — seulement {free // (1024 * 1024)} Mo libres.")]
    if free < 1024 ** 3:
        return [Check("warn", "Dossier de données", f"{path} — {free // (1024 * 1024)} Mo libres seulement.")]
    return [Check("ok", "Dossier de données", f"{path} — {gib:.1f} Go libres")]


# ---------- Contrôles REST Discord (jamais de passerelle) ----------

def build_invite_url(application_id) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={application_id}&permissions={BOT_PERMISSIONS}"
            f"&scope={quote('bot applications.commands')}")


async def _get_json(session: aiohttp.ClientSession, url: str, token: str):
    async with session.get(url, headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT},
                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = None
        return resp.status, body


async def check_discord(session: aiohttp.ClientSession, token: str) -> tuple[list[Check], Optional[str]]:
    """Valide le jeton, l'intent « Server Members », les réglages de l'application et le nombre de serveurs.
    Renvoie (contrôles, identifiant de l'application ou None si le jeton est inutilisable)."""
    if not token:
        return [], None
    checks: list[Check] = []
    try:
        status, me = await _get_json(session, f"{DISCORD_API}/users/@me", token)
    except Exception as ex:
        return [Check("fail", "Connexion à Discord", f"discord.com injoignable depuis cette machine ({ex}).")], None
    if status == 401:
        return [Check("fail", "Jeton du bot", "refusé par Discord : jeton invalide ou réinitialisé. "
                                              "Portail > Bot > Reset Token, puis recopiez-le en entier.")], None
    if status != 200 or not isinstance(me, dict):
        return [Check("fail", "Connexion à Discord", f"réponse inattendue ({status}).")], None
    checks.append(Check("ok", "Jeton du bot", f"valide (bot « {me.get('username', '?')} »)"))
    application_id = str(me["id"])

    try:
        status, app = await _get_json(session, f"{DISCORD_API}/applications/@me", token)
    except Exception:
        status, app = 0, None
    if status == 200 and isinstance(app, dict):
        application_id = str(app.get("id") or application_id)
        flags = app.get("flags")
        if isinstance(flags, int):
            if flags & (FLAG_GUILD_MEMBERS | FLAG_GUILD_MEMBERS_LIMITED):
                checks.append(Check("ok", "Intent « Server Members »", "activé"))
            else:
                checks.append(Check("fail", "Intent « Server Members »",
                                    "désactivé : le bot refusera de démarrer. Portail > Bot > Privileged Gateway Intents "
                                    "> activez « Server Members Intent »."))
        else:
            checks.append(Check("warn", "Intent « Server Members »", "impossible à vérifier automatiquement : contrôlez-le "
                                                                     "dans Portail > Bot > Privileged Gateway Intents."))
        redirects = app.get("redirect_uris")
        if isinstance(redirects, list):
            if OAUTH_REDIRECT in redirects:
                checks.append(Check("ok", "Redirection OAuth2", "déclarée"))
            else:
                checks.append(Check("fail", "Redirection OAuth2", f"manquante. Portail > OAuth2 > Redirects > ajoutez "
                                                                  f"exactement {OAUTH_REDIRECT}"))
        else:
            checks.append(Check("warn", "Redirection OAuth2", f"à vérifier à la main : Portail > OAuth2 > Redirects doit "
                                                              f"contenir exactement {OAUTH_REDIRECT}"))
        if app.get("bot_require_code_grant"):
            checks.append(Check("fail", "Invitation du bot", "« Requires OAuth2 Code Grant » est coché : désactivez-le "
                                                             "(Portail > Bot), sinon le bot ne peut pas être invité."))
    else:
        checks.append(Check("warn", "Réglages de l'application", f"non lisibles automatiquement (réponse {status})."))

    try:
        status, guilds = await _get_json(session, f"{DISCORD_API}/users/@me/guilds?limit=200", token)
    except Exception:
        status, guilds = 0, None
    if status == 200 and isinstance(guilds, list):
        if guilds:
            checks.append(Check("ok", "Serveurs Discord", f"le bot est présent sur {len(guilds)} serveur(s)"))
        else:
            checks.append(Check("warn", "Serveurs Discord", "le bot n'est encore invité sur aucun serveur : ouvrez l'URL "
                                                            "d'invitation (python server.py --invite-url)."))
    return checks, application_id


async def check_public_url(session: aiohttp.ClientSession, public_url: str) -> list[Check]:
    url = (public_url or "").strip().rstrip("/")
    if not url:
        return []
    try:
        async with session.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status == 200:
                return [Check("ok", "Joignable depuis Internet", f"{url}/health répond")]
            return [Check("warn", "Joignable depuis Internet", f"{url}/health répond {resp.status} "
                                                               "(le serveur est-il bien démarré derrière ce nom ?).")]
    except Exception as ex:
        return [Check("warn", "Joignable depuis Internet",
                      f"{url} ne répond pas depuis le serveur lui-même ({type(ex).__name__}). Normal si le serveur "
                      "ne peut pas se joindre par son adresse publique (box) ; sinon vérifiez le DNS, le port 443 "
                      "et le certificat.")]


async def run_checks(config: dict, data_dir: Path, session: aiohttp.ClientSession,
                     with_network: bool = True, with_public_check: bool = True) -> tuple[list[Check], Optional[str]]:
    """Tous les contrôles qui ne dépendent pas d'un serveur Discord précis. Renvoie (contrôles, id d'application).
    with_public_check=False : ne cherche pas à joindre public_url (avant que le serveur ne soit démarré, l'adresse
    ne peut évidemment pas répondre encore)."""
    checks = check_config(config) + check_tools() + check_data_dir(data_dir)
    application_id: Optional[str] = None
    if with_network:
        discord_checks, application_id = await check_discord(session, config.get("bot_token", ""))
        checks += discord_checks
        if with_public_check:
            checks += await check_public_url(session, config.get("public_url", ""))
    return checks, application_id


# ---------- Contrôles propres à un serveur Discord (bot en direct, pour /diagnostic) ----------

def check_guild(guild, member=None) -> list[Check]:
    """Droits du bot dans CE serveur : Voir le salon + Se connecter + Parler sur chaque salon vocal.
    `guild` / `member` = objets discord.py (accès par duck-typing, pour rester testable)."""
    checks: list[Check] = []
    me = guild.me
    if me is None:
        return [Check("warn", "Droits du bot", "bot introuvable dans la liste des membres de ce serveur.")]

    channels = list(guild.voice_channels)
    if not channels:
        checks.append(Check("info", "Salons vocaux", "ce serveur n'a aucun salon vocal."))
    else:
        problems = []
        for channel in channels:
            perms = channel.permissions_for(me)
            missing = [label for ok, label in ((perms.view_channel, "Voir le salon"),
                                               (perms.connect, "Se connecter"),
                                               (perms.speak, "Parler")) if not ok]
            if missing:
                problems.append(f"#{channel.name} ({', '.join(missing)})")
        if not problems:
            checks.append(Check("ok", "Droits du bot sur les salons vocaux",
                                f"il peut rejoindre et parler dans les {len(channels)} salon(s) vocal(aux)"))
        else:
            shown = ", ".join(problems[:6]) + (f" et {len(problems) - 6} autre(s)" if len(problems) > 6 else "")
            checks.append(Check("warn", "Droits du bot sur les salons vocaux",
                                f"manquants sur : {shown}. Paramètres du salon > Permissions > rôle du bot."))

    voice = getattr(member, "voice", None)
    if voice is not None and voice.channel is not None:
        perms = voice.channel.permissions_for(me)
        if perms.view_channel and perms.connect and perms.speak:
            checks.append(Check("ok", "Votre salon vocal actuel", f"le bot peut rejoindre « {voice.channel.name} »"))
        else:
            checks.append(Check("fail", "Votre salon vocal actuel", f"le bot ne peut pas rejoindre « {voice.channel.name} » "
                                                                    "(droits manquants) : /join échouera."))
    else:
        checks.append(Check("info", "Votre salon vocal actuel", "vous n'êtes dans aucun salon vocal ; rejoignez-en un "
                                                                "et relancez /diagnostic pour le tester."))
    return checks


# ---------- Ligne de commande : python server.py --check [--no-public-check] | --invite-url ----------

async def _cli_async(argv: list[str], config: dict, data_dir: Path) -> int:
    async with aiohttp.ClientSession() as session:
        if "--invite-url" in argv:
            token = config.get("bot_token", "")
            if not token:
                print("bot_token manquant : impossible de construire l'URL d'invitation.", file=sys.stderr)
                return 1
            checks, application_id = await check_discord(session, token)
            if application_id is None:
                print(render(checks, markdown=False), file=sys.stderr)
                return 1
            print(build_invite_url(application_id))
            return 0

        checks, application_id = await run_checks(config, data_dir, session,
                                                  with_public_check="--no-public-check" not in argv)
        print(render(checks, markdown=False))
        if application_id:
            print(f"\nURL d'invitation du bot : {build_invite_url(application_id)}")
        return 1 if has_failure(checks) else 0


def cli(argv: list[str], config: dict, data_dir: Path) -> int:
    import asyncio
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # emojis lisibles même sur une console Windows/CI
        except Exception:
            pass
    return asyncio.run(_cli_async(argv, config, data_dir))
