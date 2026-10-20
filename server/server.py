"""
Serveur WaseBoard : catalogue de sons partagé (API HTTP) + bot Discord qui les joue dans le
salon vocal. Peut être connecté à plusieurs serveurs Discord (guilds) en même temps, chacun
avec sa propre connexion vocale et son propre mixeur audio.

Voir README.md pour la mise en place complète.
"""

import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import math
import os
import secrets
import shutil
import signal
import sys
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import quote, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import web

import alerts
import audio
import diagnostics
import effects
import messages
import preferences
import reports
import webapp
# Le code audio vit dans audio.py ; ces noms restent importables depuis server (les tests lisent S.SILENCE, S.FRAME_BYTES…).
from audio import (FRAME_BYTES, PCM_BYTES_PER_MS, RTP_CLOCK_HZ, SILENCE, MixingAudioSource, PCMBufferSource,
                   PCMCache, decode_to_pcm)
from wb_config import DATA_DIR, ConfigError, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("waseboard-server")


class QuietPollingFilter(logging.Filter):
    """Retire du journal d'accès les sondages réguliers quand ils réussissent : /activity (toutes les 300 ms dans l'application, toutes les
    secondes dans la page web et l'Activité, avec ou sans « ?guild_id=… »), /status (toutes les 3 à 4 s) et la synchronisation des préférences
    (toutes les 30 à 60 s par page ou application ouverte) : des dizaines de milliers de lignes par jour et par personne, qui noyaient tout le
    reste. Les échecs (401, 5xx…) et toutes les autres requêtes restent journalisés."""

    _POLLS = ('"GET /activity ', '"GET /activity?', '"GET /status ', '"GET /status?', '"POST /me/preferences/sync ')

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (" 200 " in message and any(poll in message for poll in self._POLLS))


logging.getLogger("aiohttp.access").addFilter(QuietPollingFilter())

# Configuration : config.json + variables d'environnement WASEBOARD_* (voir wb_config.py) ; toutes les données vivent
# dans DATA_DIR (à côté de ce script par défaut, ou WASEBOARD_DATA_DIR — le volume /data en Docker).
SOUNDS_DIR = DATA_DIR / "sounds_data"
CATALOG_PATH = SOUNDS_DIR / "catalog.json"
SHARED_CATEGORIES_PATH = DATA_DIR / "shared_categories.json"
OAUTH_SESSIONS_PATH = DATA_DIR / "oauth_sessions.json"
USER_PREFERENCES_PATH = DATA_DIR / "user_preferences.json"
USER_CATEGORIES_PATH = DATA_DIR / "user_categories.json"    # 3.5.0 : catégories seules, reprises dans user_preferences.json puis mises de côté
STATS_PATH = DATA_DIR / "stats.jsonl"
ALERTS_STATE_PATH = DATA_DIR / "alerts_state.json"
GUILD_SETTINGS_PATH = DATA_DIR / "guild_settings.json"
AUDIT_PATH = DATA_DIR / "audit.jsonl"
TRASH_DIR = SOUNDS_DIR / "trash"
TRASH_PATH = SOUNDS_DIR / "trash.json"

DISCORD_API_BASE = "https://discord.com/api/v10"
# Marge de sécurité avant l'expiration réelle du token Discord sous-jacent : couvre la
# latence du round-trip de rafraîchissement, pas un éventuel décalage d'horloge (le calcul
# d'expiration est toujours relatif à time.time() local, jamais à une valeur absolue
# fournie par Discord — aucun risque de désynchronisation d'horloge possible).
DISCORD_TOKEN_EXPIRY_SKEW_SECONDS = 60

try:
    CONFIG = load_config()
except ConfigError as ex:
    raise SystemExit(str(ex))

# Le jeton n'est exigé qu'au démarrage du bot (voir __main__) : `--check` doit pouvoir tourner sans lui
# pour dire précisément ce qui manque.
BOT_TOKEN: str = CONFIG.get("bot_token", "")
GUILD_ID: Optional[int] = CONFIG.get("guild_id")
HTTP_HOST: str = CONFIG.get("http_host", "0.0.0.0")
HTTP_PORT: int = CONFIG.get("http_port", 5005)
SHARED_SECRET: str = CONFIG.get("shared_secret", "")
OAUTH2_CLIENT_ID: str = CONFIG.get("oauth2_client_id", "")  # facultatif : déduit du bot sinon (voir oauth_client_id())
OAUTH2_CLIENT_SECRET: str = CONFIG.get("oauth2_client_secret", "")
PUBLIC_URL: str = CONFIG.get("public_url", "")
# Où télécharger WaseBoard (proposé sur la page d'invitation à qui ne l'a pas encore installé).
DOWNLOAD_URL: str = CONFIG.get("download_url") or "https://github.com/salsi64/WaseBoard/releases/latest"
# Durée de conservation d'un son supprimé avant purge définitive (0 = jamais purgé).
TRASH_RETENTION_DAYS: int = CONFIG.get("trash_retention_days", 30)

# Plafonds de ressources de l'instance (0 = désactivé : comportement historique). Voir « Capacité » dans le README.
# max_sources_per_guild n'en fait PAS partie : c'est un réglage par guilde (DEFAULT_GUILD_SETTINGS plus bas),
# chaque serveur peut avoir son propre plafond de sons simultanés (ou aucun).
MAX_GUILDS: int = CONFIG.get("max_guilds", 0)                          # serveurs Discord où le bot reste présent
MAX_CONCURRENT_VOICE: int = CONFIG.get("max_concurrent_voice", 0)      # connexions vocales simultanées
MAX_FFMPEG_PROCESSES: int = CONFIG.get("max_ffmpeg_processes", 0)      # sons joués en même temps, toutes guildes (1 ffmpeg chacun)
HTTP_RATE_LIMIT_PER_MIN: int = CONFIG.get("http_rate_limit_per_min", 0)  # requêtes/min/IP sur les routes publiques
# Voyant « en train de parler » du bot : secondes sans aucun son avant que le bot cesse d'émettre (le voyant s'éteint).
# 0 (défaut) = le bot émet en continu, voyant toujours allumé mais AUCUNE latence au premier son. Voir MixingAudioSource.
VOICE_IDLE_SLEEP_S: float = CONFIG.get("voice_idle_sleep_s", 0)
audio.VOICE_IDLE_SLEEP_S = VOICE_IDLE_SLEEP_S  # lu par MixingAudioSource (module audio)
# Mémoire (Mo) réservée aux sons déjà décodés : un clic démarre alors instantanément au lieu d'attendre ~50 ms le
# démarrage de ffmpeg. 0 (défaut) = désactivé (chaque lecture lance ffmpeg). Voir PCMCache.
PCM_CACHE_MB: float = CONFIG.get("pcm_cache_mb", 0)
# Jours d'inactivité au bout desquels une session de connexion Discord est supprimée (0 = jamais) : une personne
# qui n'ouvre plus l'appli ne laisse pas ses jetons Discord sur le serveur indéfiniment.
SESSION_MAX_IDLE_DAYS: float = CONFIG.get("session_max_idle_days", 90)
# Boucles (un son qui recommence tant qu'on ne l'arrête pas) : durée maximale en minutes avant arrêt automatique ;
# 0 = boucles désactivées. Le son doit tenir en mémoire une fois décodé (LOOP_MAX_BYTES).
LOOP_MAX_MINUTES: float = CONFIG.get("loop_max_minutes", 30)
LOOP_MAX_BYTES = 32 * 1024 * 1024  # un son plus long (~170 s décodé) ne peut pas tourner en boucle
# Alertes d'exploitation (message privé au propriétaire de l'application Discord) : voir alerts.py. Un seuil à 0 désactive
# l'alerte correspondante ; alerts_enabled = false les coupe toutes.
ALERTS_ENABLED: bool = CONFIG.get("alerts_enabled", True)
ALERTS_USER_ID: str = str(CONFIG.get("alerts_user_id") or "")  # vide = le propriétaire de l'application
ALERTS_DISK_PERCENT: int = CONFIG.get("alerts_disk_percent", 90)
ALERTS_CERT_DAYS: int = CONFIG.get("alerts_cert_days", 14)
ALERTS_ERROR_BURST: int = CONFIG.get("alerts_error_burst", 20)  # erreurs en 5 minutes
# Application web (PWA) servie sous /app : voir webapp.py. Elle exige public_url (adresse de retour de la connexion Discord).
# web_redirect_uris : adresses de retour supplémentaires acceptées (autre nom de domaine, accès en réseau local…) ; chacune
# doit aussi être déclarée dans le portail Discord (OAuth2 › Redirects).
WEB_APP_ENABLED: bool = CONFIG.get("web_app_enabled", True)
WEB_REDIRECT_URIS: list = CONFIG.get("web_redirect_uris") or []
WEB_DIR = Path(__file__).parent / "web"
# Activité Discord (la même page, dans un salon vocal de Discord) : voir README. Sans effet si la page web est désactivée.
WEB_ACTIVITY_ENABLED: bool = CONFIG.get("web_activity_enabled", True)
# Commande « point d'entrée » (type 4) qui lance l'Activité : Discord ne la crée pas toujours seul (application qui avait déjà
# des commandes avant l'activation des Activités), et une synchro globale des commandes slash la supprime. handler 2 = Discord
# lance l'Activité lui-même. integration_types/contexts omis : valeurs par défaut de l'application.
ACTIVITY_ENTRY_POINT_TYPE = 4
ACTIVITY_ENTRY_POINT = {"name": "launch", "description": "Ouvre WaseBoard (la planche de sons) dans le salon vocal",
                        "type": ACTIVITY_ENTRY_POINT_TYPE, "handler": 2}
ALERTS_RECONNECT_MIN_S = 60  # une coupure de Discord plus courte n'est pas signalée

# Arrêt (systemctl stop/restart, docker stop) : le bot quitte ses salons vocaux puis ferme sa connexion à Discord, avec des délais bornés. Sans cela,
# un départ de salon vocal qui n'aboutissait pas retardait l'arrêt de 30 s, puis systemd tuait le processus (constaté à chaque arrêt pendant
# qu'un salon vocal était occupé : « The voice handshake is being terminated » puis « State 'stop-sigterm' timed out »).
SHUTDOWN_VOICE_TIMEOUT_S = 5.0   # départ d'un salon vocal
SHUTDOWN_CLOSE_TIMEOUT_S = 10.0  # fermeture de la connexion à Discord, une fois les salons quittés
ERROR_BURST = alerts.ErrorBurstDetector(ALERTS_ERROR_BURST)
logging.getLogger().addHandler(ERROR_BURST)
SEQUENCE_MAX_STEPS = 8           # sons dans un enchaînement
SEQUENCE_MAX_DELAY_MS = 10_000   # attente maximale avant UNE étape
SEQUENCE_MAX_SPAN_MS = 60_000    # délai total maximal avant la dernière étape
# Réglages imposés par l'hébergeur aux guildes : valeurs PAR DÉFAUT de celles qui n'ont encore rien enregistré
# (leurs admins peuvent les modifier) et PLAFONDS (que leurs admins ne peuvent pas dépasser ; « illimité » n'est alors
# plus permis pour ce réglage). Clés possibles : voir wb_config.GUILD_LIMIT_KEYS.
DEFAULT_GUILD_LIMITS: dict = CONFIG.get("default_guild_limits") or {}
GUILD_LIMIT_CEILINGS: dict = CONFIG.get("guild_limit_ceilings") or {}
STARTED_AT = time.time()

SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
TRASH_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma"}


#: Version du serveur, annoncée par /status (page Diagnostic du client, rapports de support).
SERVER_VERSION = "3.7.0"

#: Fonctionnalités annoncées aux clients par /status : un client récent ne propose une action que si le serveur la gère
#: (un serveur plus ancien n'envoie pas ce champ, un ancien client l'ignore).
SERVER_FEATURES = (["replace_file", "stop_sound", "sequence", "play_effects", "personal_categories", "preferences_sync"] + (["loop"] if LOOP_MAX_MINUTES > 0 else [])
                   + (["web_app"] if WEB_APP_ENABLED and webapp.origin_of(PUBLIC_URL) and (WEB_DIR / "index.html").exists() else []))

# Filet de sécurité seulement : l'activité est normalement effacée précisément à la fin
# réelle du son (voir MixingAudioSource.on_finish), pas après un délai fixe. Cette valeur ne
# sert que si ce mécanisme venait à échouer (ex: redémarrage du serveur en pleine lecture).
ACTIVITY_TTL_SECONDS = 30.0

# Durée après laquelle un client silencieux (n'ayant pas sondé /activity récemment) n'est
# plus considéré comme "application ouverte".
ONLINE_TTL_SECONDS = 12.0

# find_memberships (quelles guildes voit cet utilisateur) retombe sur guild.fetch_member — un
# vrai appel REST Discord — dès que le membre n'est pas dans le cache de la passerelle. Sans ce
# cache, des clics rapprochés (ex: plusieurs clics de suite sur un son) déclenchaient chacun ce
# fallback, et la limitation de débit de Discord sur cette route faisait croître l'attente à
# chaque appel (jusqu'à plus d'une seconde en rafale) — perçu comme une latence au clic.
MEMBERSHIP_CACHE_TTL = 30.0


class DiscordAuthRevoked(Exception):
    """Discord a répondu invalid_grant à un échange/rafraîchissement de token OAuth2 —
    l'utilisateur a révoqué l'autorisation WaseBoard, ou le code a déjà été utilisé."""


def migrate_catalog_entry(entry: dict) -> dict:
    """Rétro-compatibilité : les sons uploadés avant l'isolation par guilde n'ont pas de
    guild_id. On les rattache à la guilde configurée pour cette instance (config.json),
    en mémoire uniquement — ne réécrit jamais catalog.json depuis un chemin de lecture."""
    if not entry.get("guild_id") and GUILD_ID:
        entry["guild_id"] = str(GUILD_ID)
    return entry


def load_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    return [migrate_catalog_entry(e) for e in catalog]


def record_play_stat(
    sound_id: str,
    user_id: str,
    username: Optional[str],
    avatar_url: Optional[str],
    guild_id: Optional[int],
    guild_name: Optional[str],
) -> None:
    """Ajoute un evenement de lecture au journal de statistiques (une ligne JSON par lecture),
    uniquement pour les utilisateurs ayant renseigne leur ID Discord."""
    entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
    record = {
        "ts": time.time(),
        "user_id": user_id,
        "username": username,
        "avatar_url": avatar_url,
        "sound_id": sound_id,
        "sound_name": entry["name"] if entry else None,
        "emoji": entry.get("emoji") if entry else None,
        "guild_id": str(guild_id) if guild_id is not None else None,
        "guild_name": guild_name,
    }
    try:
        with open(STATS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("Échec de l'enregistrement des statistiques de lecture")


def _atomic_write_json(path: Path, data, private: bool = False) -> None:
    """Écriture atomique (temp + os.replace) : un crash en plein write ne laisse jamais un JSON
    tronqué à la place de l'ancien contenu."""
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if private:
        os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def save_catalog(catalog: list[dict]) -> None:
    _atomic_write_json(CATALOG_PATH, catalog)


def load_shared_categories() -> dict:
    if not SHARED_CATEGORIES_PATH.exists():
        return {}
    with open(SHARED_CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_shared_categories(data: dict) -> None:
    _atomic_write_json(SHARED_CATEGORIES_PATH, data)


# ---------- Préférences personnelles (favoris, couleurs, catégories perso et leur ordre) ----------
# Vues à trois endroits (application Windows, page web, Activité) : le serveur garde UNE copie par personne (identifiant Discord de la session),
# lisible et modifiable par elle seule, et fusionne ce que chaque appareil envoie (voir preferences.py). Rien d'autre que des noms de catégories
# et des identifiants de sons. Quand la personne n'a plus rien, son entrée est supprimée.


def _read_json_dict(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        log.exception("%s illisible : repart de zéro", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def load_user_preferences() -> dict:
    """{ identifiant Discord: { rev, updated_at, favorites, colors, categories, order } }. Les catégories envoyées par la 3.5.0
    (user_categories.json) y sont reprises pour les personnes qui n'ont pas encore d'entrée."""
    data = _read_json_dict(USER_PREFERENCES_PATH) if USER_PREFERENCES_PATH.exists() else {}
    if USER_CATEGORIES_PATH.exists():
        for user_id, old in _read_json_dict(USER_CATEGORIES_PATH).items():
            if user_id in data or not isinstance(old, dict):
                continue
            doc, _ = preferences.normalize({"categories": old.get("categories"), "order": old.get("order")})
            if doc is not None and not preferences.is_empty(doc):
                data[user_id] = {"rev": 1, "updated_at": old.get("updated_at"), **doc}
    return data


def save_user_preferences(data: dict) -> None:
    _atomic_write_json(USER_PREFERENCES_PATH, data, private=True)
    if USER_CATEGORIES_PATH.exists():   # son contenu est maintenant dans le nouveau fichier : on le met de côté (il ne doit pas ressusciter)
        try:
            os.replace(USER_CATEGORIES_PATH, USER_CATEGORIES_PATH.with_name(USER_CATEGORIES_PATH.name + ".migrated"))
        except OSError:
            log.exception("Impossible de mettre de côté %s", USER_CATEGORIES_PATH.name)


def user_preferences_doc(stored: dict) -> dict:
    """Le document d'une entrée (sans rev ni date), complété si des champs manquent."""
    doc, _ = preferences.normalize({key: stored.get(key) for key in ("favorites", "colors", "categories", "order") if stored.get(key) is not None})
    return doc if doc is not None else preferences.empty_doc()


def sync_user_preferences(user_id: str, base: Optional[dict], mine: dict) -> tuple[int, dict]:
    """Fusionne l'état d'un appareil avec la copie du serveur, l'enregistre si elle change, et renvoie (révision, document fusionné).
    Sans `await` entre la lecture et l'écriture : deux appareils qui envoient en même temps ne s'écrasent jamais."""
    everyone = load_user_preferences()
    stored = everyone.get(user_id, {})
    current = user_preferences_doc(stored)
    merged = preferences.merge(base, mine, current)
    rev = int(stored.get("rev") or 0)
    if merged != current:
        rev += 1
        if preferences.is_empty(merged):
            everyone.pop(user_id, None)   # plus rien à garder : on n'en conserve rien
        else:
            everyone[user_id] = {"rev": rev, "updated_at": time.time(), **merged}
        save_user_preferences(everyone)
    return rev, merged


def load_oauth_sessions() -> dict:
    if not OAUTH_SESSIONS_PATH.exists():
        return {}
    with open(OAUTH_SESSIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_oauth_sessions(data: dict) -> None:
    # Ce fichier est réécrit à chaque connexion ET à chaque rafraîchissement paresseux de
    # token, et contient des jetons Discord vivants : écriture atomique, lisible par le seul
    # propriétaire.
    _atomic_write_json(OAUTH_SESSIONS_PATH, data, private=True)


def purge_idle_sessions(sessions: dict, max_idle_days: float, now: Optional[float] = None) -> list[str]:
    """Retire de `sessions` celles qui n'ont servi depuis plus de `max_idle_days` jours (0 = jamais) et renvoie leurs
    jetons. Une session sans `last_used_at` (ancienne) est datée de sa création, à défaut de maintenant."""
    if max_idle_days <= 0:
        return []
    now = time.time() if now is None else now
    limit = now - max_idle_days * 86400
    expired = [token for token, session in sessions.items()
               if (session.get("last_used_at") or session.get("created_at") or now) < limit]
    for token in expired:
        del sessions[token]
    return expired


# ---------- Réglages par guilde, journal d'actions, corbeille ----------

DEFAULT_GUILD_SETTINGS = {
    "admin_role_id": None,        # rôle Discord (id en str) considéré admin en plus de propriétaire/Administrateur
    "upload_admins_only": False,  # True : seuls les admins peuvent uploader dans cette guilde
    "max_sounds": 0,              # 0 = illimité (sons natifs de la guilde)
    "max_file_mb": 0,             # 0 = pas de limite propre à la guilde
    "max_duration_s": 0,          # 0 = pas de limite
    "max_total_mb": 0,            # 0 = pas de limite d'espace disque cumulé (sons natifs de la guilde)
    "play_rate_per_min": 0,       # 0 = pas d'anti-spam à la lecture
    "max_sources_per_guild": 0,   # 0 = pas de limite de sons différents joués en même temps sur cette guilde
    "blocked_uploaders": [],      # ids Discord (str) interdits d'upload sur cette guilde
}


def load_all_guild_settings() -> dict:
    if not GUILD_SETTINGS_PATH.exists():
        return {}
    with open(GUILD_SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_guild_ceilings(settings: dict) -> dict:
    """Applique les plafonds de l'hébergeur (GUILD_LIMIT_CEILINGS) : pour un réglage plafonné, « illimité » (0)
    devient le plafond et toute valeur supérieure y est ramenée. Sans plafond configuré, ne change rien."""
    for key, ceiling in GUILD_LIMIT_CEILINGS.items():
        if ceiling > 0:
            value = settings.get(key, 0)
            settings[key] = ceiling if value == 0 else min(value, ceiling)
    return settings


def get_guild_settings(guild_id, all_settings: Optional[dict] = None, clamp: bool = True) -> dict:
    """Réglages complets d'une guilde : valeurs par défaut, puis (seulement pour une guilde qui n'a encore rien
    enregistré) les valeurs par défaut de l'hébergeur, puis ce que l'admin a enregistré, puis les plafonds de
    l'hébergeur (clamp=False les ignore : valeurs brutes, pour pouvoir les réécrire telles quelles).
    `all_settings` permet de ne lire le fichier qu'une fois quand on traite plusieurs guildes."""
    if all_settings is None:
        all_settings = load_all_guild_settings()
    saved = all_settings.get(str(guild_id))
    merged = {**DEFAULT_GUILD_SETTINGS, **(DEFAULT_GUILD_LIMITS if saved is None else {}), **(saved or {})}
    merged["blocked_uploaders"] = [str(u) for u in merged["blocked_uploaders"]]
    return apply_guild_ceilings(merged) if clamp else merged


def guild_used_bytes(guild_id, catalog: Optional[list] = None) -> tuple[int, int]:
    """(nombre de sons, octets sur disque) des sons NATIFS d'une guilde (la corbeille n'est pas comptée : elle est
    purgée automatiquement)."""
    count = total = 0
    for entry in (load_catalog() if catalog is None else catalog):
        if entry.get("guild_id") != str(guild_id):
            continue
        count += 1
        try:
            total += (SOUNDS_DIR / f"{entry['id']}{entry['extension']}").stat().st_size
        except OSError:
            pass
    return count, total


def save_guild_settings(guild_id, settings: dict) -> None:
    all_settings = load_all_guild_settings()
    all_settings[str(guild_id)] = settings
    _atomic_write_json(GUILD_SETTINGS_PATH, all_settings)


def record_audit(
    guild_id,
    actor_id,
    actor_name: Optional[str],
    action: str,
    sound_id: Optional[str] = None,
    sound_name: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Ajoute une ligne au journal d'actions (une ligne JSON par action) — consultable par les
    admins de la guilde concernée."""
    record = {
        "ts": time.time(),
        "guild_id": str(guild_id) if guild_id is not None else None,
        "actor_id": str(actor_id) if actor_id is not None else None,
        "actor_name": actor_name,
        "action": action,
        "sound_id": sound_id,
        "sound_name": sound_name,
        "details": details or {},
    }
    try:
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.exception("Échec de l'écriture du journal d'actions")


def is_guild_admin(guild, member, settings: Optional[dict] = None) -> bool:
    """Admin d'une guilde = propriétaire du serveur Discord, OU permission Discord
    « Administrateur », OU membre du rôle désigné dans les réglages de la guilde."""
    if member.id == guild.owner_id:
        return True
    if member.guild_permissions.administrator:
        return True
    if settings is None:
        settings = get_guild_settings(guild.id)
    role_id = settings.get("admin_role_id")
    return bool(role_id) and any(str(r.id) == str(role_id) for r in member.roles)


def compute_permissions(memberships) -> dict[str, dict]:
    """Droits de l'utilisateur sur chacune de ses guildes : {guild_id: {is_admin, can_upload}}.
    `memberships` = liste de (guilde, membre). Les quotas (nombre/taille/durée) ne sont pas
    inclus : ils dépendent de l'état courant et sont vérifiés à l'upload."""
    all_settings = load_all_guild_settings()
    perms: dict[str, dict] = {}
    for guild, member in memberships:
        settings = get_guild_settings(guild.id, all_settings)
        admin = is_guild_admin(guild, member, settings)
        can_upload = admin or (not settings["upload_admins_only"] and str(member.id) not in settings["blocked_uploaders"])
        perms[str(guild.id)] = {"is_admin": admin, "can_upload": can_upload}
    return perms


def is_sound_mine(entry: dict, user_id) -> bool:
    return entry.get("uploaded_by") is not None and entry["uploaded_by"] == str(user_id)


def can_edit_sound(entry: dict, user_id, perms: dict[str, dict]) -> bool:
    """Renommer / changer l'emoji / supprimer : membre de la guilde NATIVE du son, et auteur
    du son ou admin de cette guilde. Un ancien son (sans auteur) n'est donc gérable que par un admin."""
    native = perms.get(str(entry.get("guild_id")))
    return native is not None and (native["is_admin"] or is_sound_mine(entry, user_id))


def can_share_sound(entry: dict, user_id, target_guild_id: str, perms: dict[str, dict]) -> bool:
    """Ajouter/retirer un son à la catégorie partagée de la guilde cible : membre de la cible,
    et auteur du son ou admin de la cible."""
    target = perms.get(str(target_guild_id))
    return target is not None and (target["is_admin"] or is_sound_mine(entry, user_id))


def _read_jsonl(path: Path):
    """Itère sur les lignes JSON valides d'un fichier .jsonl (inexistant = vide, lignes corrompues ignorées)."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def count_plays_per_sound() -> Counter:
    """Nombre total de lectures par son (toutes guildes confondues, depuis le début du journal)."""
    return Counter(r.get("sound_id") for r in _read_jsonl(STATS_PATH))


def aggregate_play_stats(guild_id: str, days: int, catalog_names: dict[str, dict]) -> dict:
    """Statistiques de lecture dans le vocal de cette guilde sur les `days` derniers jours.
    `catalog_names` : {sound_id: entrée du catalogue}, pour afficher le nom ACTUEL d'un son renommé."""
    now = datetime.now()
    first_day = (now - timedelta(days=days - 1)).date()
    cutoff = datetime.combine(first_day, datetime.min.time()).timestamp()

    sounds: Counter = Counter()
    sound_meta: dict[str, dict] = {}
    users: Counter = Counter()
    user_meta: dict[str, dict] = {}
    per_day: Counter = Counter()
    total = 0

    for r in _read_jsonl(STATS_PATH):
        if r.get("guild_id") != guild_id or r.get("ts", 0) < cutoff:
            continue
        total += 1
        sid, uid = r.get("sound_id"), r.get("user_id")
        sounds[sid] += 1
        sound_meta[sid] = r
        users[uid] += 1
        user_meta[uid] = r
        per_day[datetime.fromtimestamp(r["ts"]).date().isoformat()] += 1

    def sound_row(sid: str, count: int) -> dict:
        current = catalog_names.get(sid)
        recorded = sound_meta[sid]
        return {
            "sound_id": sid,
            "name": (current or {}).get("name") or recorded.get("sound_name") or "(son supprimé)",
            "emoji": (current or {}).get("emoji") or recorded.get("emoji"),
            "count": count,
        }

    return {
        "days": days,
        "total_plays": total,
        "top_sounds": [sound_row(sid, c) for sid, c in sounds.most_common(10)],
        "top_users": [
            {"user_id": uid, "username": user_meta[uid].get("username"),
             "avatar_url": user_meta[uid].get("avatar_url"), "count": c}
            for uid, c in users.most_common(10)
        ],
        "per_day": [
            {"date": (first_day + timedelta(days=i)).isoformat(),
             "count": per_day.get((first_day + timedelta(days=i)).isoformat(), 0)}
            for i in range(days)
        ],
    }


def read_audit(guild_id: str, limit: int, before: Optional[float]) -> list[dict]:
    """Dernières actions de cette guilde, la plus récente d'abord."""
    entries = [r for r in _read_jsonl(AUDIT_PATH)
               if r.get("guild_id") == guild_id and (before is None or r.get("ts", 0) < before)]
    entries.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return entries[:limit]


async def probe_duration_seconds(path: Path) -> Optional[float]:
    """Durée d'un fichier audio via ffprobe (livré avec ffmpeg). None si ffprobe est absent ou
    illisible : la limite de durée est alors simplement ignorée plutôt que de bloquer l'upload."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        return float(stdout.decode().strip())
    except Exception:
        return None


MIN_TRIM_MS = 100
MAX_TRIM_MS = 3_600_000  # 1 h : garde-fou contre des valeurs absurdes, pas une vraie limite de durée


def parse_trim(raw_start, raw_end) -> Optional[tuple[int, int]]:
    """Découpe non destructive d'un son : (début, fin) en millisecondes, ou None si aucune (les deux
    absents/vides = son entier). Lève ValueError avec un message lisible si incohérent."""
    def blank(value) -> bool:
        return value is None or str(value).strip() == ""

    if blank(raw_start) and blank(raw_end):
        return None
    if blank(raw_start) or blank(raw_end):
        raise ValueError("La découpe demande un début ET une fin.")
    try:
        start, end = int(str(raw_start).strip()), int(str(raw_end).strip())
    except ValueError:
        raise ValueError("Le début et la fin de la découpe doivent être des nombres de millisecondes.")
    if start < 0 or end > MAX_TRIM_MS or end - start < MIN_TRIM_MS:
        raise ValueError("Découpe invalide : la portion gardée doit durer au moins 0,1 s.")
    return start, end


def trim_ffmpeg_options(entry: dict) -> tuple[Optional[str], Optional[str]]:
    """(before_options, options) à passer à FFmpegPCMAudio pour ne jouer que la portion gardée.
    Le fichier stocké reste l'original complet : -ss avant l'entrée (positionnement) et -t après
    (durée) donnent un découpage précis, y compris sur du MP3 à débit variable."""
    start, end = entry.get("trim_start_ms"), entry.get("trim_end_ms")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return None, None
    return f"-ss {start / 1000:.3f}", f"-t {(end - start) / 1000:.3f}"


def load_trash() -> list[dict]:
    if not TRASH_PATH.exists():
        return []
    with open(TRASH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_trash(trash: list[dict]) -> None:
    _atomic_write_json(TRASH_PATH, trash)


def trash_sound(sound_id: str, deleted_by) -> Optional[dict]:
    """Supprime un son du catalogue en le plaçant à la corbeille (fichier conservé, partages
    mémorisés pour une éventuelle restauration). Synchrone et sans await : le chargement et la
    réécriture du catalogue ne peuvent pas s'entrecroiser avec une autre requête. Chaque étape
    laisse un état récupérable si le processus s'arrête en route (le fichier d'origine n'est
    supprimé qu'en dernier). Renvoie l'entrée supprimée, ou None si le son n'existe plus."""
    catalog = load_catalog()
    entry = next((s for s in catalog if s["id"] == sound_id), None)
    if entry is None:
        return None

    source = SOUNDS_DIR / f"{sound_id}{entry['extension']}"
    if source.exists():
        shutil.copy2(source, TRASH_DIR / source.name)

    shared = load_shared_categories()
    shared_in = [gid for gid, ids in shared.items() if sound_id in ids]

    trash = load_trash()
    trash.append({**entry, "deleted_by": str(deleted_by), "deleted_at": time.time(), "shared_in": shared_in})
    save_trash(trash)

    save_catalog([s for s in catalog if s["id"] != sound_id])
    if shared_in:
        for gid in shared_in:
            shared[gid].remove(sound_id)
        save_shared_categories(shared)

    if source.exists():
        source.unlink()
    return entry


def restore_sound(sound_id: str) -> Optional[dict]:
    """Inverse de trash_sound : remet le fichier, l'entrée du catalogue et ses partages.
    Renvoie l'entrée restaurée, ou None si elle n'est pas (ou plus) à la corbeille."""
    trash = load_trash()
    record = next((t for t in trash if t["id"] == sound_id), None)
    if record is None:
        return None

    entry = {k: v for k, v in record.items() if k not in ("deleted_by", "deleted_at", "shared_in")}
    trashed_file = TRASH_DIR / f"{sound_id}{entry['extension']}"
    restored_file = SOUNDS_DIR / trashed_file.name
    if trashed_file.exists():
        shutil.copy2(trashed_file, restored_file)

    catalog = load_catalog()
    if not any(s["id"] == sound_id for s in catalog):
        catalog.append(entry)
        save_catalog(catalog)

    shared_in = record.get("shared_in", [])
    if shared_in:
        shared = load_shared_categories()
        for gid in shared_in:
            ids = shared.setdefault(gid, [])
            if sound_id not in ids:
                ids.append(sound_id)
        save_shared_categories(shared)

    save_trash([t for t in trash if t["id"] != sound_id])
    if trashed_file.exists():
        trashed_file.unlink()
    return entry


def purge_expired_trash(now: Optional[float] = None) -> list[dict]:
    """Supprime définitivement les sons restés à la corbeille plus de TRASH_RETENTION_DAYS jours
    (0 = jamais). Renvoie les enregistrements purgés, pour le journal d'actions."""
    if TRASH_RETENTION_DAYS <= 0:
        return []
    cutoff = (now if now is not None else time.time()) - TRASH_RETENTION_DAYS * 86400
    trash = load_trash()
    expired = [t for t in trash if t.get("deleted_at", 0) < cutoff]
    if not expired:
        return []
    for record in expired:
        (TRASH_DIR / f"{record['id']}{record['extension']}").unlink(missing_ok=True)
    expired_ids = {t["id"] for t in expired}
    save_trash([t for t in trash if t["id"] not in expired_ids])
    return expired


intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True
intents.members = True  # requis pour résoudre un membre par id ; à activer aussi dans le
                         # Portail Développeur > Bot > "Server Members Intent".


# ---------- Limitation de débit des routes publiques ----------

class PlayRefused(Exception):
    """Lecture refusée (plafond de ressources, anti-spam, son impossible à préparer) : un message lisible, rendu soit en
    réponse HTTP (route /play), soit en message Discord (commande /play)."""

    def __init__(self, message: str, status: int = 429, retry_after: Optional[int] = None, retry_header: bool = False) -> None:
        super().__init__(message)
        self.message, self.status, self.retry_after, self.retry_header = message, status, retry_after, retry_header

    def response(self) -> web.Response:
        body: dict = {"error": self.message}
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        headers = {"Retry-After": str(self.retry_after)} if self.retry_header and self.retry_after is not None else None
        return web.json_response(body, status=self.status, headers=headers)


class CapacityError(Exception):
    """L'instance a atteint un de ses plafonds de ressources (message destiné à l'utilisateur)."""


def client_ip(request: web.Request) -> str:
    """Adresse du client. Derrière un reverse proxy local (nginx, Caddy, conteneur voisin : pair en boucle locale ou en
    réseau privé), c'est la DERNIÈRE entrée de X-Forwarded-For — celle que le proxy a lui-même constatée ; les entrées
    précédentes peuvent avoir été forgées par le client."""
    peer = request.remote or "?"
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if ip.is_loopback or ip.is_private:
        last = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            pass
    return peer


class RateLimiter:
    """Fenêtre glissante de 60 s par clé (ex : (route, IP)). limit <= 0 : désactivé."""

    def __init__(self, limit_per_min: int) -> None:
        self.limit = limit_per_min
        self._hits: dict[tuple, deque] = {}

    def check(self, key: tuple) -> Optional[int]:
        """None si la requête peut passer (et la comptabilise), sinon le nombre de secondes à attendre."""
        if self.limit <= 0:
            return None
        now = time.monotonic()
        history = self._hits.setdefault(key, deque())
        while history and now - history[0] >= 60:
            history.popleft()
        if len(history) >= self.limit:
            return max(1, int(60 - (now - history[0]) + 0.999))
        history.append(now)
        if len(self._hits) > 10000:  # garde-fou mémoire : on oublie les clés dont la fenêtre est vide
            for stale in [k for k, h in self._hits.items() if not h or now - h[-1] >= 60]:
                del self._hits[stale]
        return None


RATE_LIMITER = RateLimiter(HTTP_RATE_LIMIT_PER_MIN)


def rate_limit_bucket(request: web.Request) -> Optional[str]:
    """Seules les routes que n'importe qui peut atteindre sans session valide, ou qui coûtent cher (upload), sont limitées :
    le client sonde /activity toutes les 300 ms en usage normal, ces routes-là ne doivent jamais être freinées."""
    path = request.path
    if request.method == "GET" and path.startswith("/connect/"):
        return "connect"
    if path.startswith("/oauth/"):
        return "oauth"
    if request.method == "POST" and path == "/sounds":
        return "upload"
    if request.method == "PUT" and path.startswith("/sounds/") and path.endswith("/file"):
        return "upload"  # remplacer un fichier coûte autant qu'un ajout : même seau
    return None


@web.middleware
async def language_middleware(request: web.Request, handler):
    """Rédige les messages d'erreur dans la langue du client (en-tête Accept-Language, voir messages.py). Le code du serveur écrit ses
    messages en français : un appelant sans l'en-tête, ou qui demande le français, les reçoit tels quels."""
    response = await handler(request)
    language = messages.pick_language(request.headers.get("Accept-Language"))
    if (language != messages.DEFAULT_LANGUAGE and isinstance(response, web.Response) and response.status >= 400
            and response.content_type == "application/json" and response.body):
        try:
            data = json.loads(response.body)
        except ValueError:
            return response
        if isinstance(data, dict) and isinstance(data.get("error"), str):
            translated = messages.translate(data["error"], language)
            if translated != data["error"]:
                data["error"] = translated
                response.body = json.dumps(data).encode("utf-8")
    return response


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    bucket = rate_limit_bucket(request)
    if bucket is not None:
        wait = RATE_LIMITER.check((bucket, client_ip(request)))
        if wait is not None:
            return web.json_response({"error": f"Trop de requêtes : réessayez dans {wait} s.", "retry_after": wait},
                                     status=429, headers={"Retry-After": str(wait)})
    return await handler(request)


# ---------- Lien d'invitation ----------
# Discord ne rend pas cliquable un lien « waseboard:// » (schéma personnalisé) : le message du bouton porte donc un
# vrai bouton-lien https vers une petite page de CE serveur (/connect/<code>), qui redirige vers l'appli. Le code
# est aléatoire, à durée limitée (réutilisable pendant ce délai : le navigateur redemande parfois la permission
# d'ouvrir l'appli) ; le secret partagé n'apparaît jamais dans l'URL, seulement dans la page, une fois le code validé.
INVITE_CODE_TTL_SECONDS = 15 * 60
INVITE_CODES: dict[str, float] = {}  # code -> expiration (epoch, en mémoire seulement)


def create_invite_code() -> str:
    now = time.time()
    for code in [code for code, expires in INVITE_CODES.items() if expires <= now]:
        del INVITE_CODES[code]
    if len(INVITE_CODES) >= 1000:  # garde-fou mémoire : on retire les plus anciens
        for code in sorted(INVITE_CODES, key=INVITE_CODES.get)[: len(INVITE_CODES) - 999]:
            del INVITE_CODES[code]
    code = secrets.token_urlsafe(16)
    INVITE_CODES[code] = now + INVITE_CODE_TTL_SECONDS
    return code


def invite_code_is_valid(code: str) -> bool:
    expires = INVITE_CODES.get(code)
    return expires is not None and expires > time.time()


def build_connect_link() -> str:
    """Le lien waseboard://connect qui pré-remplit l'adresse et le jeton dans l'appli."""
    return f"waseboard://connect?url={quote(PUBLIC_URL, safe='')}&token={quote(SHARED_SECRET, safe='')}"


_CONNECT_PAGE_STYLE = (
    "body{font-family:system-ui,Segoe UI,sans-serif;background:#0f1624;color:#eaf0fa;margin:0;"
    "display:flex;min-height:100vh;align-items:center;justify-content:center}"
    "main{max-width:460px;padding:32px;text-align:center}h1{font-size:22px;margin:0 0 12px}"
    "p{line-height:1.5;color:#b8c4d9;margin:0 0 16px}"
    ".btn{display:inline-block;background:#38bdf8;color:#0b1220;font-weight:600;text-decoration:none;"
    "padding:12px 22px;border-radius:10px;margin:8px 0 18px}"
    "textarea{width:100%;box-sizing:border-box;height:72px;background:#182236;color:#eaf0fa;border:1px solid #26324a;"
    "border-radius:8px;padding:8px;font-size:12px}a.dl{color:#38bdf8}small{color:#7f8da6}"
)


# Textes de la page d'invitation, par langue (la langue vient de l'en-tête Accept-Language du navigateur, voir messages.py).
_CONNECT_TEXTS = {
    "fr": {
        "gone_title": "Ce lien n'est plus valide",
        "gone_text": "Il a expiré (ou n'a jamais existé). Retournez sur Discord et cliquez de nouveau sur « Recevoir mon lien WaseBoard » pour en obtenir un nouveau.",
        "title": "Ouvrir WaseBoard",
        "text": "WaseBoard va s'ouvrir et se connecter tout seul à ce serveur. Si votre navigateur demande l'autorisation d'ouvrir l'application, acceptez.",
        "button": "Ouvrir WaseBoard",
        "download_lead": "WaseBoard n'est pas encore installé ?",
        "download_link": "Téléchargez-le ici",
        "download_tail": "puis revenez cliquer sur le bouton.",
        "copy_hint": "Rien ne se passe ? Copiez ce lien, puis collez-le dans la fenêtre Exécuter de Windows (touches Windows + R).",
    },
    "en": {
        "gone_title": "This link is no longer valid",
        "gone_text": "It has expired (or never existed). Go back to Discord and click “Get my WaseBoard link” again to get a new one.",
        "title": "Open WaseBoard",
        "text": "WaseBoard will open and connect to this server by itself. If your browser asks for permission to open the app, accept.",
        "button": "Open WaseBoard",
        "download_lead": "WaseBoard isn't installed yet?",
        "download_link": "Download it here",
        "download_tail": "then come back and click the button.",
        "copy_hint": "Nothing happens? Copy this link, then paste it into the Windows Run window (Windows + R keys).",
    },
}


def render_connect_page(link: Optional[str], nonce: str = "", language: str = "fr") -> str:
    """Page d'invitation, dans la langue demandée (français par défaut). link=None : code inconnu ou expiré."""
    text = _CONNECT_TEXTS.get(language, _CONNECT_TEXTS["fr"])
    if link is None:
        body = f"<h1>{text['gone_title']}</h1><p>{text['gone_text']}</p>"
        script = ""
    else:
        body = (
            f"<h1>{text['title']}</h1>"
            f"<p>{text['text']}</p>"
            f'<a class="btn" href="{html.escape(link)}">{text["button"]}</a>'
            f'<p>{text["download_lead"]} <a class="dl" href="{html.escape(DOWNLOAD_URL)}">{text["download_link"]}</a>, '
            f"{text['download_tail']}</p>"
            f"<p><small>{text['copy_hint']}</small></p>"
            f'<textarea readonly onclick="this.select()">{html.escape(link)}</textarea>'
        )
        script = f'<script nonce="{nonce}">setTimeout(function(){{location.href={json.dumps(link)}}},400)</script>'
    lang_code = language if language in _CONNECT_TEXTS else "fr"
    return (
        f'<!doctype html><html lang="{lang_code}"><head><meta charset="utf-8"><title>WaseBoard</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        f"<style>{_CONNECT_PAGE_STYLE}</style></head><body><main>{body}</main>{script}</body></html>"
    )


# Résumé affiché par le bouton ❓ Aide du panneau (message éphémère) : la version longue vit dans
# docs/FAQ-utilisateurs.md, partagée avec le README du dépôt — ce texte-ci doit rester cohérent avec elle.
PANEL_HELP_TEXT = (
    "**Comment utiliser WaseBoard**\n"
    "1. Rejoignez un salon vocal sur Discord.\n"
    "2. Cliquez **🔊 Rejoindre mon vocal** (ici ou dans l'application) : le bot vous rejoint.\n"
    "3. Dans l'application WaseBoard, cliquez un son : il joue chez tout le monde dans ce salon.\n\n"
    "Rien ne s'entend ? Vérifiez que le bot est bien dans **votre** salon (bouton 🔊 ci-dessus). "
    "Pas encore installé WaseBoard ? Cliquez le bouton 📬 pour recevoir votre lien de connexion.\n"
    "Guide complet : demandez le lien à un administrateur de ce serveur."
)


def tr(interaction: discord.Interaction, text):
    """Traduit un texte du bot selon la langue de Discord de la personne (`interaction.locale`) : français pour le français, anglais sinon
    (voir messages.py). Un texte absent du catalogue reste tel quel."""
    if not isinstance(text, str):
        return text
    return messages.translate(text, messages.discord_language(getattr(interaction, "locale", None)))


class MessagesTranslator(app_commands.Translator):
    """Noms, descriptions et choix des commandes affichés en anglais dans le menu de Discord pour toute personne dont Discord n'est pas en français
    (le français reste la langue du code : c'est le texte par défaut, montré aux personnes dont Discord est en français)."""

    async def translate(self, string: app_commands.locale_str, locale: discord.Locale, context: app_commands.TranslationContextTypes) -> Optional[str]:
        if locale is discord.Locale.french:
            return None
        text = string.message
        if context.location is app_commands.TranslationContextLocation.command_name:
            return messages.COMMAND_NAMES_EN.get(text)
        if context.location is app_commands.TranslationContextLocation.parameter_name:
            return messages.PARAMETER_NAMES_EN.get(text)
        translated = messages.translate(text, "en")
        return translated if translated != text else None


async def _handle_join_voice_button(interaction: discord.Interaction) -> None:
    """Logique du bouton "Rejoindre mon vocal", partagée par MemberPanelView et BotSetupView (deux
    boutons différents, même comportement — évite de la dupliquer)."""
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
        await interaction.response.send_message(
            tr(interaction, "Connectez-vous d'abord à un salon vocal sur Discord, puis recliquez ce bouton."), ephemeral=True)
        return
    channel = member.voice.channel
    try:
        await bot.join_guild_voice(interaction.guild_id, channel)
    except CapacityError as ex:
        await interaction.response.send_message(tr(interaction, f"⚠️ {ex}"), ephemeral=True)
        return
    await interaction.response.send_message(
        tr(interaction, f"Connecté à **{channel.name}**. Prêt à jouer les sons envoyés par WaseBoard."), ephemeral=True)


class MemberPanelView(discord.ui.View):
    """Panneau de boutons persistant (voir /panneau, et setup_hook : bot.add_view) — une alternative aux
    commandes slash pour les membres qui ne les utilisent pas spontanément. Posté une fois par un admin dans
    un salon, fonctionne ensuite pour tout le monde et survit aux redémarrages (custom_id fixes)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Rejoindre mon vocal", emoji="🔊", style=discord.ButtonStyle.primary,
                        custom_id="waseboard_panel_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_join_voice_button(interaction)

    @discord.ui.button(label="Aide", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="waseboard_panel_help")
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(tr(interaction, PANEL_HELP_TEXT), ephemeral=True)


class InviteView(discord.ui.View):
    """Panneau persistant (voir /configurer-invitation et setup_hook : bot.add_view) : un bouton de
    téléchargement (lien statique) à côté d'un bouton de connexion directe à CE serveur précis (code
    généré à chaque clic). Le lien de connexion mène à la même adresse + même secret partagé pour
    tout le monde ; seule la visibilité du message qui porte ce panneau est restreinte, nativement
    via les permissions du salon Discord où l'admin l'a posté — aucun contrôle de rôle à faire ici."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="⬇ Télécharger WaseBoard", style=discord.ButtonStyle.link, url=DOWNLOAD_URL))

    @discord.ui.button(label="🚀 Connexion directe à ce serveur", style=discord.ButtonStyle.secondary,
                        custom_id="waseboard_invite_button")
    async def send_link(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not PUBLIC_URL or not SHARED_SECRET:
            await interaction.response.send_message(
                tr(interaction, "Lien non configuré côté serveur (public_url/shared_secret manquants dans config.json)."),
                ephemeral=True,
            )
            return
        page_url = f"{PUBLIC_URL.rstrip('/')}/connect/{create_invite_code()}"
        link_view = discord.ui.View()
        link_view.add_item(discord.ui.Button(label="🚀 Ouvrir WaseBoard", style=discord.ButtonStyle.link, url=page_url))
        await interaction.response.send_message(
            tr(interaction, "Cliquez sur le bouton : WaseBoard s'ouvre et se connecte directement à CE serveur "
            f"(utile si votre application est reliée à une autre instance — le lien expire dans "
            f"{INVITE_CODE_TTL_SECONDS // 60} minutes)."),
            view=link_view,
            ephemeral=True,
        )


class BotSetupView(discord.ui.View):
    """Panneau tout-en-un (voir /bot-setup) : démarrer (télécharger + connexion directe à CE
    serveur), actions membres (rejoindre le vocal, aide), ajouter le bot à un autre serveur, et
    liens utiles (site, communauté, code source) — pour n'avoir qu'une seule commande à taper
    plutôt que /configurer-invitation + /panneau + /inviter-bot séparément. Persistant (voir
    setup_hook : bot.add_view). Les boutons liens sont tous des chaînes fixes (pas de dépendance à
    l'état du bot à la construction) pour pouvoir être enregistrés dès setup_hook, avant que le
    bot soit complètement prêt ; "Ajouter à un autre serveur" calcule son URL à chaque clic plutôt
    qu'à la construction, pour la même raison (voir oauth_client_id)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="⬇ Télécharger WaseBoard", style=discord.ButtonStyle.link, url=DOWNLOAD_URL, row=0))
        self.add_item(discord.ui.Button(label="🌐 Site", style=discord.ButtonStyle.link, url="https://waseboard.salsi.bid", row=2))
        self.add_item(discord.ui.Button(label="💬 Discord WaseBoard", style=discord.ButtonStyle.link, url="https://discord.gg/HAGTNGFyQd", row=2))
        self.add_item(discord.ui.Button(label="📦 GitHub", style=discord.ButtonStyle.link, url="https://github.com/salsi64/WaseBoard", row=2))

    @discord.ui.button(label="🚀 Connexion directe à ce serveur", style=discord.ButtonStyle.secondary,
                        custom_id="waseboard_setup_connect", row=0)
    async def send_link(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not PUBLIC_URL or not SHARED_SECRET:
            await interaction.response.send_message(
                tr(interaction, "Lien non configuré côté serveur (public_url/shared_secret manquants dans config.json)."),
                ephemeral=True,
            )
            return
        page_url = f"{PUBLIC_URL.rstrip('/')}/connect/{create_invite_code()}"
        link_view = discord.ui.View()
        link_view.add_item(discord.ui.Button(label="🚀 Ouvrir WaseBoard", style=discord.ButtonStyle.link, url=page_url))
        await interaction.response.send_message(
            tr(interaction, "Cliquez sur le bouton : WaseBoard s'ouvre et se connecte directement à CE serveur "
            f"(utile si votre application est reliée à une autre instance — le lien expire dans "
            f"{INVITE_CODE_TTL_SECONDS // 60} minutes)."),
            view=link_view,
            ephemeral=True,
        )

    @discord.ui.button(label="Rejoindre mon vocal", emoji="🔊", style=discord.ButtonStyle.primary,
                        custom_id="waseboard_setup_join", row=1)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_join_voice_button(interaction)

    @discord.ui.button(label="Aide", emoji="❓", style=discord.ButtonStyle.secondary,
                        custom_id="waseboard_setup_help", row=1)
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(tr(interaction, PANEL_HELP_TEXT), ephemeral=True)

    @discord.ui.button(label="➕ Ajouter à un autre serveur", style=discord.ButtonStyle.secondary,
                        custom_id="waseboard_setup_invite", row=3)
    async def invite_other_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        invite_url = diagnostics.build_invite_url(bot.oauth_client_id())
        invite_view = discord.ui.View()
        invite_view.add_item(discord.ui.Button(label="➕ Ajouter WaseBoard à mon serveur", style=discord.ButtonStyle.link, url=invite_url))
        await interaction.response.send_message(
            tr(interaction, "Ajoutez ce bot WaseBoard à un **autre** serveur Discord (le vôtre ou celui d'un ami) — chaque "
            "serveur obtient sa **propre bibliothèque de sons**, totalement indépendante des autres : pratique "
            "si vous gérez plusieurs communautés Discord. Il faut avoir le droit « Gérer le serveur » sur ce "
            "nouveau serveur pour l'ajouter."),
            view=invite_view,
            ephemeral=True,
        )


def format_bytes(number: float) -> str:
    for unit in ("octets", "Ko", "Mo", "Go"):
        if number < 1024 or unit == "Go":
            return f"{number:.0f} {unit}" if unit == "octets" else f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} Go"


def format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days} j {hours} h"
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def active_limits_text() -> str:
    """Résumé des plafonds actifs de l'instance (ceux à 0 / absents ne sont pas listés)."""
    parts = []
    for label, value in (("serveurs", MAX_GUILDS), ("salons vocaux", MAX_CONCURRENT_VOICE),
                         ("sons au total", MAX_FFMPEG_PROCESSES)):
        if value > 0:
            parts.append(f"{label} : {value}")
    if HTTP_RATE_LIMIT_PER_MIN > 0:
        parts.append(f"requêtes publiques : {HTTP_RATE_LIMIT_PER_MIN}/min/IP")
    if GUILD_LIMIT_CEILINGS:
        parts.append("plafonds des serveurs : " + ", ".join(f"{k}={v}" for k, v in GUILD_LIMIT_CEILINGS.items()))
    if DEFAULT_GUILD_LIMITS:
        parts.append("valeurs par défaut des nouveaux serveurs : " + ", ".join(f"{k}={v}" for k, v in DEFAULT_GUILD_LIMITS.items()))
    return " · ".join(parts) if parts else "aucun (instance sans limite)"


def render_instance_stats(stats: dict) -> str:
    def capacity(value: int, limit: int) -> str:
        return f"{value} / {limit}" if limit > 0 else str(value)

    latency = f"{stats['latency_ms']} ms" if stats["latency_ms"] is not None else "—"
    lines = [
        f"**Instance WaseBoard** — en ligne depuis {format_duration(stats['uptime_s'])} · latence Discord {latency}",
        f"• Serveurs Discord : {capacity(stats['guilds'], MAX_GUILDS)}",
        f"• Salons vocaux actifs : {capacity(stats['voice'], MAX_CONCURRENT_VOICE)}",
        f"• Sons en lecture (processus ffmpeg) : {capacity(stats['sources'], MAX_FFMPEG_PROCESSES)}",
        f"• Applications ouvertes en ce moment : {stats['online_users']}",
        f"• Catalogue : {stats['catalog_sounds']} sons — {format_bytes(stats['bytes_total'])} "
        f"(corbeille : {format_bytes(stats['bytes_trash'])})",
        f"• Disque : {format_bytes(stats['disk_free'])} libres",
    ]
    if stats["top_guilds"]:
        lines.append("• Serveurs les plus lourds : " + " ; ".join(
            f"{g['name']} ({g['sounds']} sons, {format_bytes(g['bytes'])})" for g in stats["top_guilds"]))
    lines.append(f"• Plafonds actifs : {active_limits_text()}")
    return "\n".join(lines)


class WaseBoardServer(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)
        # Une connexion vocale + un mixeur par serveur Discord (guild_id -> ...), pour
        # pouvoir être présent sur plusieurs Discords différents en même temps.
        self.voice_clients_map: dict[int, discord.VoiceClient] = {}
        self.mixers: dict[int, MixingAudioSource] = {}
        # Activité récente : sound_id -> {user_id: {"username", "avatar_url", "expires_at"}}
        self.active_plays: dict[str, dict[str, dict]] = {}
        # Présence : user_id -> {"username", "avatar_url", "last_seen"} (application ouverte)
        self.online_users: dict[str, dict] = {}
        # Résolution des guildes d'un utilisateur (find_memberships) : user_id -> (horodatage, résultat).
        # Voir MEMBERSHIP_CACHE_TTL.
        self._membership_cache: dict[int, tuple[float, list]] = {}
        self._activity_lock = threading.Lock()
        self._web_runner: Optional[web.AppRunner] = None
        self._shutdown_started = False                 # un seul arrêt à la fois (signal, puis fin de bot.run)
        self._shutdown_done = asyncio.Event()
        self._signal_close_task: Optional[asyncio.Task] = None
        # Sons décodés en mémoire (voir PCMCache) ; un seul fil pour les décoder en arrière-plan.
        self.pcm_cache = PCMCache(int(PCM_CACHE_MB * 1024 * 1024))
        self._pcm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pcm-decode")

        # Sessions OAuth2 Discord : session_token (émis par WaseBoard) -> identité vérifiée +
        # jetons Discord. Chargées depuis disque pour survivre à un redémarrage.
        self.oauth_sessions: dict[str, dict] = load_oauth_sessions()
        self._session_refresh_locks: dict[str, asyncio.Lock] = {}
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._commands_synced = False  # on_ready peut se rejouer à chaque reconnexion
        self._trash_purge_task: Optional[asyncio.Task] = None
        # Anti-spam à la lecture : (guild_id, user_id) -> horodatages (monotonic) des lectures récentes
        self._play_history: dict[tuple[int, str], deque] = {}
        # Étapes d'enchaînement programmées mais pas encore parties : guild_id -> {TimerHandle: id du son}.
        self._pending_steps: dict[int, dict] = {}
        # Alertes d'exploitation (voir alerts.py) : gestionnaire, tâche périodique, état de la connexion à Discord.
        self.alerts: Optional[alerts.AlertManager] = None
        self._alerts_task: Optional[asyncio.Task] = None
        self._ready_seen = False
        self._gateway_down_since: Optional[float] = None
        self.web_app: Optional[webapp.WebApp] = None  # application web (PWA) sous /app, si activée et complète

    def _install_signal_handlers(self) -> None:
        """SIGTERM (systemctl stop/restart, docker stop) : arrêt propre PENDANT que la connexion à Discord est encore ouverte. Avec le
        gestionnaire de main() (qui lève KeyboardInterrupt), les tâches de la passerelle étaient annulées avant que les salons vocaux ne soient
        quittés : le départ attendait en vain une réponse de Discord, jusqu'à 30 s. Windows (développement) ne gère pas ce mode : main() garde
        alors son gestionnaire de repli."""
        def request_close() -> None:
            self._signal_close_task = asyncio.ensure_future(self.close())
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, request_close)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    async def _leave_voice_channels(self) -> None:
        """Quitte tous les salons vocaux en parallèle ; un salon qui ne répond pas ne retarde pas les autres ni l'arrêt."""
        async def leave(client) -> None:
            try:
                await asyncio.wait_for(client.disconnect(force=True), SHUTDOWN_VOICE_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning("Salon vocal %s : le départ prend trop de temps (%s s), on passe à la suite.", getattr(getattr(client, "channel", None), "id", "?"),
                            SHUTDOWN_VOICE_TIMEOUT_S)
            except Exception:
                log.exception("Échec du départ d'un salon vocal à l'arrêt")
        clients = list(self.voice_clients)
        if clients:
            await asyncio.gather(*(leave(client) for client in clients))

    async def setup_hook(self) -> None:
        self._install_signal_handlers()
        self.http_session = aiohttp.ClientSession()
        self._trash_purge_task = asyncio.create_task(self._trash_purge_loop())
        if ALERTS_ENABLED:
            self.alerts = alerts.AlertManager(alerts.AlertState(ALERTS_STATE_PATH), self._send_owner_dm)
            self._alerts_task = asyncio.create_task(self._alerts_loop())

        # Enregistrement persistant (custom_id fixe) : le bouton reste fonctionnel sur le
        # message déjà posté par /configurer-invitation après un redémarrage, sans avoir à
        # retenir où il a été posté.
        self.add_view(InviteView())
        self.add_view(MemberPanelView())
        self.add_view(BotSetupView())
        await self.tree.set_translator(MessagesTranslator())  # avant toute synchronisation des commandes (voir plus bas)

        if not GUILD_ID:
            await self.tree.sync()
            log.info("Commandes slash synchronisées globalement (peut prendre jusqu'à 1h à apparaître).")
        # Avec guild_id configuré : synchronisation instantanée PAR serveur, faite dans on_ready /
        # on_guild_join (self.guilds n'est pas encore rempli à ce stade).
        await self._ensure_activity_entry_point()

        await self._start_http_server()

    def oauth_client_id(self) -> str:
        """Identifiant client OAuth2 : celui de config.json s'il est renseigné, sinon celui de l'application
        Discord du bot (c'est le même nombre) — l'admin n'a plus que le secret client à copier."""
        if OAUTH2_CLIENT_ID:
            return OAUTH2_CLIENT_ID
        application_id = self.application_id or (self.user.id if self.user else None)
        return str(application_id) if application_id else ""

    def web_invite_url(self) -> str:
        """Adresse d'ajout du bot à un serveur Discord, proposée dans les paramètres de la page web. Vide quand l'instance est pleine
        (max_guilds atteint : le bot refuserait le nouveau serveur, autant ne pas le proposer) ou sans identifiant d'application."""
        client_id = self.oauth_client_id()
        if not client_id or (MAX_GUILDS > 0 and len(self.guilds) >= MAX_GUILDS):
            return ""
        return diagnostics.build_invite_url(client_id)

    async def _ensure_activity_entry_point(self) -> None:
        """Crée la commande qui lance l'Activité si l'application n'en a pas (sans elle, le lanceur d'activités de Discord affiche
        « aucune commande enregistrée pour les lancer »). Jamais bloquant : un échec (Activités pas encore activées au portail,
        réseau) est journalisé et le serveur démarre quand même ; l'essai est refait au prochain démarrage."""
        application_id = self.application_id or (self.user.id if self.user else None)
        if not (WEB_APP_ENABLED and WEB_ACTIVITY_ENABLED and application_id):
            return
        try:
            existing = await self.http.get_global_commands(application_id)
            present = [c for c in existing if c.get("type") == ACTIVITY_ENTRY_POINT_TYPE]
            if present:
                log.info("Activité Discord : commande de lancement déjà présente (« %s », mode %s).", present[0].get("name"), present[0].get("handler"))
                return
            await self.http.upsert_global_command(application_id, dict(ACTIVITY_ENTRY_POINT))
            log.info("Activité Discord : commande de lancement « %s » créée (à choisir dans le lanceur d'activités d'un salon vocal).",
                     ACTIVITY_ENTRY_POINT["name"])
        except Exception as exc:  # noqa: BLE001 — jamais bloquant au démarrage
            log.warning("Activité Discord : commande de lancement non créée (%s). Vérifiez que les Activités sont activées au portail "
                        "Discord (voir « Activité Discord » dans le README).", str(exc)[:200])

    async def _sync_commands_to_guild(self, guild: discord.abc.Snowflake) -> None:
        """Rend les commandes slash disponibles immédiatement sur ce serveur (la synchro globale
        met jusqu'à 1h à se propager). Sans ça, seul le serveur de guild_id aurait /join & co —
        le bot étant multi-serveurs, tous ceux où il est présent doivent les avoir."""
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
        await self._alerts_on_ready()
        if not GUILD_ID or self._commands_synced:
            return
        self._commands_synced = True
        for guild in self.guilds:
            try:
                await self._sync_commands_to_guild(guild)
                log.info("Commandes slash synchronisées sur le serveur %s (%s).", guild.id, guild.name)
            except Exception:
                log.exception("Échec de la synchronisation des commandes sur %s", guild.id)

    async def _decline_guild(self, guild: discord.Guild) -> None:
        """Instance pleine (MAX_GUILDS) : prévient le propriétaire du serveur qui vient d'inviter le bot (message privé,
        s'il les accepte) puis le quitte. Les serveurs déjà présents ne sont jamais touchés."""
        log.warning("Capacité maximale atteinte (%s serveurs) : « %s » (%s) est refusé.", MAX_GUILDS, guild.name, guild.id)
        text = (f"Cette instance WaseBoard a atteint sa capacité maximale ({MAX_GUILDS} serveurs Discord) : le bot quitte "
                f"« {guild.name} ». Vous pouvez réessayer plus tard, ou héberger votre propre instance (voir la documentation).")
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            await owner.send(text)
        except Exception:
            log.info("Message de refus non remis au propriétaire de %s (messages privés fermés ?).", guild.id)
        try:
            await guild.leave()
        except Exception:
            log.exception("Impossible de quitter le serveur refusé %s", guild.id)

    async def _welcome_new_guild(self, guild: discord.Guild) -> None:
        """Message de bienvenue (DM au propriétaire, même mécanisme que _decline_guild) quand un
        nouveau serveur accepte le bot. Moins critique depuis le serveur par défaut intégré au
        client (les membres n'ont plus besoin d'aucune action de l'admin pour se connecter), mais
        reste utile pour que l'admin découvre /panneau pour ses membres."""
        text = (
            f"👋 Merci d'avoir ajouté **WaseBoard** à **{guild.name}** !\n\n"
            "WaseBoard est un soundboard partagé : vos membres cliquent un son dans l'application, il "
            "joue à la fois sur leurs enceintes et dans le salon vocal Discord, pour tout le monde.\n\n"
            f"**Vos membres n'ont rien de spécial à faire** : télécharger le client (<{DOWNLOAD_URL}>) "
            "et se connecter avec Discord suffit, l'application se connecte automatiquement à cette "
            "instance.\n\n"
            "Pour aller plus loin : tapez `/panneau` dans un salon pour y poster des boutons simples "
            "(rejoindre le vocal, stop) utilisables par tous sans rien installer.\n\n"
            "Guide complet : <https://github.com/salsi64/WaseBoard/blob/master/docs/FAQ-utilisateurs.md>\n"
            "Besoin d'aide ? <https://discord.gg/HAGTNGFyQd>"
        )
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            await owner.send(text)
        except Exception:
            log.info("Message de bienvenue non remis au propriétaire de %s (messages privés fermés ?).", guild.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if MAX_GUILDS > 0 and len(self.guilds) > MAX_GUILDS:
            await self._decline_guild(guild)
            return
        await self._welcome_new_guild(guild)
        if not GUILD_ID:
            return  # synchro globale : déjà valable pour ce nouveau serveur
        try:
            await self._sync_commands_to_guild(guild)
            log.info("Commandes slash synchronisées sur le nouveau serveur %s (%s).", guild.id, guild.name)
        except Exception:
            log.exception("Échec de la synchronisation des commandes sur %s", guild.id)

    # ---------- Alertes d'exploitation ----------

    def _instance_footer(self) -> str:
        """Ligne discrète ajoutée à chaque alerte : de quelle instance elle vient (test, production…) et sa version."""
        where = urlparse(PUBLIC_URL).netloc if PUBLIC_URL else f"port {HTTP_PORT}"
        return f"\n-# Instance : {where} · v{SERVER_VERSION}"

    async def _send_owner_dm(self, text: str) -> bool:
        """Remet une alerte à la personne qui héberge (alerts_user_id, sinon le propriétaire de l'application Discord).
        False si les messages privés sont fermés ou que Discord est injoignable : l'appelant réessaiera plus tard."""
        try:
            if ALERTS_USER_ID:
                recipient_id = int(ALERTS_USER_ID)
            else:
                info = await self.application_info()
                recipient_id = info.team.owner_id if info.team else info.owner.id
            user = await self.fetch_user(recipient_id)
            await user.send(text + self._instance_footer())
            return True
        except Exception as ex:
            log.warning("Alerte non remise (messages privés fermés ou Discord injoignable) : %s", ex)
            return False

    async def _alerts_on_ready(self) -> None:
        """Premier démarrage : signale un arrêt non propre de la fois précédente. Démarrages suivants (nouvelle session
        après une coupure) : signale la reconnexion."""
        if self.alerts is None:
            return
        if self._ready_seen:
            await self._alerts_reconnected()
            return
        self._ready_seen = True
        state = self.alerts.state
        unexpected = state.clean_shutdown is False
        state.clean_shutdown = False  # remis à True par close() ; resté False = le processus n'a pas pu s'arrêter proprement
        state.save()
        if unexpected:
            await self.alerts.notify("restart", alerts.restart_alert(SERVER_VERSION))

    async def _alerts_reconnected(self) -> None:
        since, self._gateway_down_since = self._gateway_down_since, None
        if self.alerts is None or since is None:
            return
        down = time.monotonic() - since
        if down >= ALERTS_RECONNECT_MIN_S:
            await self.alerts.notify("reconnect", alerts.reconnect_alert(down))

    async def on_disconnect(self) -> None:
        if self._gateway_down_since is None:
            self._gateway_down_since = time.monotonic()

    async def on_resumed(self) -> None:
        await self._alerts_reconnected()

    async def _alerts_loop(self) -> None:
        """Contrôles périodiques (voir _alerts_tick) : un tour par minute, après un délai laissant le démarrage se terminer."""
        await asyncio.sleep(90)
        due = {"disk": 0.0, "cert": 0.0}
        while True:
            try:
                await self._alerts_tick(due)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Échec d'un contrôle d'alerte", exc_info=True)  # WARNING : ne doit pas compter dans la rafale d'erreurs
            await asyncio.sleep(60)

    async def _alerts_tick(self, due: dict) -> None:
        """Un tour de contrôles : rafale d'erreurs (à chaque tour), disque (toutes les 10 min), certificat HTTPS (toutes les
        6 h). `due` : prochaines échéances (horloge monotone) du disque et du certificat, mises à jour ici."""
        if self.alerts is None:
            return
        burst = ERROR_BURST.burst()
        if burst is not None:
            await self.alerts.notify("errors", alerts.error_burst_alert(burst[0], ERROR_BURST.window_s, burst[1]))
        now = time.monotonic()
        if now >= due["disk"]:
            due["disk"] = now + 600
            message = alerts.disk_alert(DATA_DIR, ALERTS_DISK_PERCENT)
            if message:
                await self.alerts.notify("disk", message)
        if now >= due["cert"]:
            due["cert"] = now + 6 * 3600
            message = await asyncio.to_thread(alerts.certificate_alert, PUBLIC_URL, ALERTS_CERT_DAYS)
            if message:
                await self.alerts.notify("cert", message)

    async def close(self) -> None:
        if self._shutdown_started:   # déjà en cours (signal reçu, puis fin de bot.run) : on attend la fin du premier arrêt
            await self._shutdown_done.wait()
            return
        self._shutdown_started = True
        try:
            if self._alerts_task is not None:
                self._alerts_task.cancel()
            if self.alerts is not None:
                self.alerts.state.clean_shutdown = True  # arrêt voulu (systemctl stop/restart, Ctrl+C) : pas d'alerte au prochain démarrage
                self.alerts.state.save()
            if self._trash_purge_task is not None:
                self._trash_purge_task.cancel()
            await self._leave_voice_channels()
            if self.http_session is not None:
                await self.http_session.close()
            try:
                await asyncio.wait_for(super().close(), SHUTDOWN_CLOSE_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning("Fermeture de la connexion à Discord trop longue (%s s) : arrêt forcé du reste.", SHUTDOWN_CLOSE_TIMEOUT_S)
        finally:
            self._shutdown_done.set()

    async def _trash_purge_loop(self) -> None:
        """Purge périodique de la corbeille (voir TRASH_RETENTION_DAYS) : au démarrage, puis toutes les 6 h."""
        while True:
            try:
                for record in purge_expired_trash():
                    record_audit(record.get("guild_id"), None, "système", "purge",
                                 record["id"], record.get("name"),
                                 {"deleted_by": record.get("deleted_by")})
                    log.info("Corbeille : « %s » purgé définitivement (%s).", record.get("name"), record["id"])
            except Exception:
                log.exception("Échec de la purge de la corbeille")
            try:
                expired = purge_idle_sessions(self.oauth_sessions, SESSION_MAX_IDLE_DAYS)
                if expired:
                    save_oauth_sessions(self.oauth_sessions)
                    log.info("Sessions inactives depuis plus de %g jours supprimées : %d.", SESSION_MAX_IDLE_DAYS, len(expired))
            except Exception:
                log.exception("Échec de la purge des sessions inactives")
            await asyncio.sleep(6 * 3600)

    # ---------- Salons vocaux ----------

    def active_voice_count(self) -> int:
        return sum(1 for vc in self.voice_clients_map.values() if vc.is_connected())

    def active_source_count(self) -> int:
        """Sons en cours de lecture sur toutes les guildes (= processus ffmpeg vivants)."""
        return sum(mixer.count() for mixer in list(self.mixers.values()))

    @staticmethod
    def _storage_usage() -> dict:
        """Occupation disque (bloquant : à lancer dans un thread) : total des sons, corbeille, détail par guilde."""
        per_guild: dict[str, list[int]] = {}
        total = 0
        for entry in load_catalog():
            try:
                size = (SOUNDS_DIR / f"{entry['id']}{entry['extension']}").stat().st_size
            except OSError:
                continue
            slot = per_guild.setdefault(str(entry.get("guild_id")), [0, 0])
            slot[0] += 1
            slot[1] += size
            total += size
        trash = 0
        if TRASH_DIR.exists():
            for item in TRASH_DIR.iterdir():
                try:
                    if item.is_file():
                        trash += item.stat().st_size
                except OSError:
                    pass
        return {"total": total, "trash": trash, "per_guild": per_guild, "disk_free": shutil.disk_usage(DATA_DIR).free}

    async def instance_stats(self) -> dict:
        """Chiffres de santé/capacité de cette instance (commande /instance, réservée à son propriétaire)."""
        usage = await asyncio.to_thread(self._storage_usage)
        top = []
        for guild_id, (count, size) in sorted(usage["per_guild"].items(), key=lambda kv: kv[1][1], reverse=True)[:5]:
            guild = self.get_guild(int(guild_id)) if guild_id.isdigit() else None
            top.append({"name": guild.name if guild else f"serveur inconnu ({guild_id})", "sounds": count, "bytes": size})
        return {
            "uptime_s": time.time() - STARTED_AT,
            "latency_ms": round(self.latency * 1000) if self.latency == self.latency else None,  # NaN avant la connexion
            "guilds": len(self.guilds),
            "voice": self.active_voice_count(),
            "sources": self.active_source_count(),
            "online_users": len(self.get_online_snapshot()),
            "catalog_sounds": len(load_catalog()),
            "bytes_total": usage["total"], "bytes_trash": usage["trash"], "disk_free": usage["disk_free"],
            "top_guilds": top,
        }

    def _play_capacity_error(self, sound_id: str, guild_id: int, mixer: "MixingAudioSource") -> Optional[str]:
        """Message de refus si lancer ce son dépasserait un plafond de ressources, sinon None. Rejouer un son déjà en
        lecture le REMPLACE (voir MixingAudioSource.add) : cela ne consomme rien de plus, donc n'est jamais refusé."""
        max_sources = get_guild_settings(guild_id)["max_sources_per_guild"]
        if max_sources <= 0 and MAX_FFMPEG_PROCESSES <= 0:
            return None
        if mixer.has(sound_id):
            return None
        if max_sources > 0 and mixer.count() >= max_sources:
            return f"Trop de sons en même temps sur ce serveur (maximum {max_sources}) : réessayez dans un instant."
        if MAX_FFMPEG_PROCESSES > 0 and self.active_source_count() >= MAX_FFMPEG_PROCESSES:
            return "Le serveur WaseBoard est très sollicité en ce moment : réessayez dans un instant."
        return None

    async def join_guild_voice(self, guild_id: int, channel: discord.VoiceChannel) -> discord.VoiceClient:
        existing = self.voice_clients_map.get(guild_id)
        if existing is not None and existing.is_connected():
            await existing.move_to(channel)
            return existing
        if MAX_CONCURRENT_VOICE > 0 and self.active_voice_count() >= MAX_CONCURRENT_VOICE:
            raise CapacityError(f"Ce serveur WaseBoard est saturé ({MAX_CONCURRENT_VOICE} salons vocaux actifs en même "
                                "temps) : réessayez dans quelques minutes.")

        vc = await channel.connect()
        self.voice_clients_map[guild_id] = vc
        mixer = MixingAudioSource()
        mixer.attach(vc)
        self.mixers[guild_id] = mixer
        vc.play(mixer)  # lecture persistante : les sons s'ajoutent au mixeur, ne "remplacent" jamais rien
        return vc

    async def leave_guild_voice(self, guild_id: int) -> bool:
        vc = self.voice_clients_map.pop(guild_id, None)
        self.mixers.pop(guild_id, None)
        if vc is not None and vc.is_connected():
            await vc.disconnect()
            return True
        return False

    def resolve_guild_by_user(self, user_id: int) -> Optional[int]:
        """Retrouve automatiquement le serveur Discord ciblé : celui où le bot est connecté
        ET où cet utilisateur est actuellement présent dans le salon vocal."""
        for guild_id, vc in self.voice_clients_map.items():
            if vc.is_connected() and any(m.id == user_id for m in vc.channel.members):
                return guild_id
        return None

    def resolve_guild_id(self, raw_guild_id: Optional[str], raw_user_id: Optional[str], strict: bool = False) -> Optional[int]:
        if raw_guild_id:
            try:
                return int(raw_guild_id)
            except ValueError:
                pass

        if raw_user_id:
            try:
                found = self.resolve_guild_by_user(int(raw_user_id))
                if found is not None:
                    return found
            except ValueError:
                pass

        # Filet de sécurité pour /play et /stop uniquement : ignoré en mode strict (indicateurs
        # de présence), où un seul salon connecté ne veut pas dire que l'utilisateur y est.
        if not strict and len(self.voice_clients_map) == 1:
            return next(iter(self.voice_clients_map))
        return None

    # ---------- Membres Discord ----------

    def find_member(self, user_id: int) -> Optional[discord.Member]:
        """Cherche un membre dans le cache local uniquement (rapide, pas d'appel réseau)."""
        for guild in self.guilds:
            member = guild.get_member(user_id)
            if member is not None:
                return member
        return None

    async def find_member_async(self, user_id: int) -> Optional[discord.Member]:
        """Comme find_member, mais interroge Discord si absent du cache. Plus lent : réservé aux
        cas où la précision prime (résolution d'identité), pas au sondage d'activité fréquent."""
        member = self.find_member(user_id)
        if member is not None:
            return member

        for guild in self.guilds:
            try:
                member = await guild.fetch_member(user_id)
                if member is not None:
                    return member
            except (discord.NotFound, discord.HTTPException):
                continue
        return None

    async def find_memberships(self, user_id: int) -> list[tuple[discord.Guild, discord.Member]]:
        """Retrouve TOUS les serveurs Discord dont cet utilisateur est membre (avec son membre
        sur chacun, pour les contrôles de rôle) — chacun aura automatiquement sa propre
        catégorie partagée, indépendamment de toute présence vocale. Résultat mis en cache
        (MEMBERSHIP_CACHE_TTL) : voir la constante pour pourquoi."""
        cached = self._membership_cache.get(user_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < MEMBERSHIP_CACHE_TTL:
            return cached[1]

        member_by_guild_id: dict[int, discord.Member] = {}
        guilds_to_fetch: list[discord.Guild] = []
        for guild in self.guilds:
            member = guild.get_member(user_id)
            if member is not None:
                member_by_guild_id[guild.id] = member
            elif not getattr(guild, "chunked", False):
                guilds_to_fetch.append(guild)
            # Sinon : le cache de membres de ce serveur est COMPLET (intent « Membres » + chargement des membres,
            # voir Guild.chunked) — absent du cache veut dire « pas membre », inutile de demander à Discord.
            # Sans cette règle, tout compte absent d'un des serveurs du bot coûtait ~270 ms (un appel REST) au
            # premier clic suivant chaque expiration du cache (MEMBERSHIP_CACHE_TTL).

        if guilds_to_fetch:
            async def _safe_fetch(guild: discord.Guild):
                try:
                    return guild.id, await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    return guild.id, None

            # En parallèle (pas guilde par guilde) : un cache froid coûte alors le temps du PLUS
            # LENT appel REST, pas leur somme — c'est ce qui rendait perceptible le tout premier
            # clic (ou le premier après un moment d'inactivité, une fois le cache expiré).
            started = time.perf_counter()
            for gid, member in await asyncio.gather(*(_safe_fetch(g) for g in guilds_to_fetch)):
                if member is not None:
                    member_by_guild_id[gid] = member
            log.info("Résolution des membres : %d appel(s) REST à Discord (%s) en %.0f ms.", len(guilds_to_fetch),
                     ", ".join(str(getattr(g, "name", g.id)) for g in guilds_to_fetch), (time.perf_counter() - started) * 1000)

        found = [(guild, member_by_guild_id[guild.id]) for guild in self.guilds if guild.id in member_by_guild_id]
        self._membership_cache[user_id] = (now, found)
        if len(self._membership_cache) > 500:  # garde-fou bas coût contre une croissance illimitée
            expired = [uid for uid, (ts, _) in self._membership_cache.items() if now - ts >= MEMBERSHIP_CACHE_TTL]
            for uid in expired:
                del self._membership_cache[uid]
        return found

    async def find_all_guilds_for_user(self, user_id: int) -> list[discord.Guild]:
        return [guild for guild, _ in await self.find_memberships(user_id)]

    async def resolve_visible_guild_ids(self, user_id: int) -> set[str]:
        """Guildes (en str) dont cet utilisateur est membre, parmi celles où le bot est présent."""
        guilds = await self.find_all_guilds_for_user(user_id)
        return {str(g.id) for g in guilds}

    async def user_access(self, user_id) -> tuple[set[str], dict[str, dict]]:
        """(guildes visibles, droits par guilde) d'un utilisateur — une seule résolution des
        membres Discord par requête."""
        memberships = await self.find_memberships(int(user_id))
        return {str(g.id) for g, _ in memberships}, compute_permissions(memberships)

    @staticmethod
    def catalog_visible_to(catalog: list[dict], visible_guild_ids: set[str]) -> list[dict]:
        """Filtre un catalogue aux sons natifs des guildes de l'utilisateur, plus ceux
        partagés dans ces guildes via shared_categories.json. Fail-closed : aucune guilde
        résolue => liste vide plutôt que le catalogue complet (jamais de fuite cross-guild)."""
        if not visible_guild_ids:
            return []
        shared = load_shared_categories()
        shared_ids = {sid for gid in visible_guild_ids for sid in shared.get(gid, [])}
        return [e for e in catalog if e.get("guild_id") in visible_guild_ids or e["id"] in shared_ids]

    @staticmethod
    def sound_visible_to(entry: dict, visible_guild_ids: set[str]) -> bool:
        if not visible_guild_ids:
            return False
        if entry.get("guild_id") in visible_guild_ids:
            return True
        shared = load_shared_categories()
        return any(entry["id"] in shared.get(gid, []) for gid in visible_guild_ids)

    async def find_current_voice_channel(self, user_id: int) -> Optional[tuple[int, discord.VoiceChannel]]:
        """Cherche, sur TOUS les serveurs Discord du bot (pas seulement ceux où il est déjà
        connecté en vocal), le salon vocal où cet utilisateur se trouve actuellement — pour le
        bouton "Rejoindre mon vocal" côté client."""
        for guild in self.guilds:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    continue
            if member is not None and member.voice is not None and member.voice.channel is not None:
                return guild.id, member.voice.channel
        return None

    # ---------- OAuth2 Discord ----------

    async def _discord_token_request(self, payload: dict) -> dict:
        """POST vers l'endpoint token de Discord (échange de code ou rafraîchissement).
        Lève DiscordAuthRevoked si Discord répond invalid_grant (code déjà utilisé, ou
        refresh_token révoqué par l'utilisateur), sinon une exception générique."""
        async with self.http_session.post(
            f"{DISCORD_API_BASE}/oauth2/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            body = await resp.json()
            if resp.status != 200:
                if body.get("error") == "invalid_grant":
                    raise DiscordAuthRevoked(body.get("error_description", "invalid_grant"))
                raise RuntimeError(f"Discord a répondu {resp.status} : {body}")
            return body

    async def _refresh_identity_from_discord(self, session: dict) -> None:
        """Appelle /users/@me avec le token d'accès de la session et met à jour
        user_id/username/avatar_url en place."""
        async with self.http_session.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bearer {session['discord_access_token']}"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"/users/@me a répondu {resp.status}")
            me = await resp.json()
        session["user_id"] = str(me["id"])
        session["username"] = me.get("global_name") or me.get("username", "")
        avatar_hash = me.get("avatar")
        session["avatar_url"] = (
            f"https://cdn.discordapp.com/avatars/{me['id']}/{avatar_hash}.png"
            if avatar_hash else None
        )

    async def _ensure_discord_token_fresh(self, token: str, session: dict) -> bool:
        """Rafraîchit le token Discord sous-jacent si son expiration approche. Renvoie False
        si la session a dû être révoquée (refresh_token invalide/retiré côté Discord — force
        une reconnexion propre plutôt que de faire échouer silencieusement chaque requête)."""
        if session["discord_token_expires_at"] - time.time() > DISCORD_TOKEN_EXPIRY_SKEW_SECONDS:
            return True

        lock = self._session_refresh_locks.setdefault(token, asyncio.Lock())
        async with lock:
            # Une requête concurrente a pu déjà rafraîchir pendant l'attente du verrou.
            if session["discord_token_expires_at"] - time.time() > DISCORD_TOKEN_EXPIRY_SKEW_SECONDS:
                return True
            try:
                data = await self._discord_token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": session["discord_refresh_token"],
                    "client_id": self.oauth_client_id(),
                    "client_secret": OAUTH2_CLIENT_SECRET,
                })
            except DiscordAuthRevoked:
                self.oauth_sessions.pop(token, None)
                save_oauth_sessions(self.oauth_sessions)
                return False
            except Exception:
                # Échec transitoire (réseau vers discord.com) : garde l'ancien token (encore
                # valide pendant la marge) plutôt que de déconnecter sur un blip réseau.
                log.exception("Échec transitoire du rafraîchissement du jeton Discord")
                return True

            session["discord_access_token"] = data["access_token"]
            session["discord_refresh_token"] = data.get("refresh_token", session["discord_refresh_token"])
            session["discord_token_expires_at"] = time.time() + data["expires_in"]
            try:
                await self._refresh_identity_from_discord(session)
            except Exception:
                log.exception("Échec de la mise à jour d'identité après rafraîchissement")
            save_oauth_sessions(self.oauth_sessions)
            return True

    async def _require_session(self, request: web.Request, required: bool = True):
        """Renvoie (session, None) si l'en-tête Authorization porte une session valide,
        sinon (None, réponse_401). required=False laisse passer l'absence totale d'en-tête
        (utilisé par les endpoints accessibles avant toute connexion Discord) ; un en-tête
        présent mais invalide/révoqué est TOUJOURS un 401, quel que soit `required` — c'est
        le signal que le client utilise pour déclencher une reconnexion propre."""
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            if required:
                return None, web.json_response({"error": "unauthorized", "reason": "missing_session"}, status=401)
            return None, None

        token = auth[len("Bearer "):].strip()
        session = self.oauth_sessions.get(token)
        if session is None:
            return None, web.json_response({"error": "unauthorized", "reason": "invalid_session"}, status=401)

        session["last_used_at"] = time.time()
        if not await self._ensure_discord_token_fresh(token, session):
            return None, web.json_response({"error": "unauthorized", "reason": "revoked"}, status=401)
        return session, None

    # ---------- Activité (highlight + avatars partagés) & présence ----------

    def record_activity(self, sound_id: str, user_id: str, username: Optional[str], avatar_url: Optional[str], guild_id: int,
                        ttl: Optional[float] = None) -> None:
        with self._activity_lock:
            self.active_plays.setdefault(sound_id, {})[user_id] = {
                "username": username or "?",
                "avatar_url": avatar_url or "",
                "expires_at": time.time() + (ACTIVITY_TTL_SECONDS if ttl is None else ttl),
                "guild_id": guild_id,
            }

    def clear_activity(self, sound_id: str, user_id: str) -> None:
        """Efface le highlight d'un utilisateur pour un son précis, dès que ce son a réellement
        fini de jouer (appelé depuis le thread audio du mixeur, jamais depuis l'event loop)."""
        with self._activity_lock:
            users = self.active_plays.get(sound_id)
            if users and user_id in users:
                del users[user_id]
                if not users:
                    self.active_plays.pop(sound_id, None)

    def get_active_snapshot(self, guild_id: Optional[int] = None) -> dict:
        """guild_id=None (appelant non identifié ou pas en vocal) renvoie rien plutôt que tout."""
        now = time.time()
        result: dict[str, list[dict]] = {}
        with self._activity_lock:
            for sound_id, users in list(self.active_plays.items()):
                alive = {uid: info for uid, info in users.items() if info["expires_at"] > now}
                if not alive:
                    self.active_plays.pop(sound_id, None)
                    continue

                self.active_plays[sound_id] = alive
                if guild_id is None:
                    continue

                same_channel = {uid: info for uid, info in alive.items() if info.get("guild_id") == guild_id}
                if same_channel:
                    result[sound_id] = [
                        {"user_id": uid, "username": info["username"], "avatar_url": info["avatar_url"]}
                        for uid, info in same_channel.items()
                    ]
        return result

    def record_presence(self, user_id: str, username: str, avatar_url: str) -> None:
        with self._activity_lock:
            self.online_users[user_id] = {
                "username": username, "avatar_url": avatar_url, "last_seen": time.time()
            }

    def get_online_snapshot(self) -> list[dict]:
        now = time.time()
        with self._activity_lock:
            alive = {uid: info for uid, info in self.online_users.items() if now - info["last_seen"] < ONLINE_TTL_SECONDS}
            self.online_users = alive
            return [{"user_id": uid, "username": info["username"], "avatar_url": info["avatar_url"]} for uid, info in alive.items()]

    async def _start_http_server(self) -> None:
        app = web.Application(client_max_size=64 * 1024 * 1024,  # 64 Mo max par upload
                              middlewares=[language_middleware] + ([rate_limit_middleware] if RATE_LIMITER.limit > 0 else []))
        app.router.add_get("/sounds", self._handle_list_sounds)
        app.router.add_get("/sounds/{id}/file", self._handle_get_file)
        app.router.add_post("/sounds", self._handle_upload_sound)
        app.router.add_patch("/sounds/{id}", self._handle_update_sound)
        app.router.add_put("/sounds/{id}/file", self._handle_replace_sound_file)
        app.router.add_delete("/sounds/{id}", self._handle_delete_sound)
        app.router.add_post("/play", self._handle_play)
        app.router.add_post("/stop", self._handle_stop_all)
        app.router.add_post("/play-sequence", self._handle_play_sequence)
        app.router.add_post("/join-my-channel", self._handle_join_my_channel)
        app.router.add_get("/status", self._handle_status)
        app.router.add_get("/health", self._handle_health)  # publique et minimale : sonde Docker/Caddy/supervision
        app.router.add_get("/connect/{code}", self._handle_connect_page)  # publique : voir _handle_connect_page
        app.router.add_get("/activity", self._handle_activity)
        app.router.add_get("/my-guilds", self._handle_my_guilds)
        app.router.add_get("/shared-categories", self._handle_get_shared_categories)
        app.router.add_post("/shared-categories/sounds", self._handle_shared_category_sound)
        app.router.add_get("/me/preferences", self._handle_get_preferences)
        app.router.add_post("/me/preferences/sync", self._handle_sync_preferences)
        app.router.add_get("/me/categories", self._handle_get_my_categories)
        app.router.add_put("/me/categories", self._handle_put_my_categories)
        app.router.add_get("/admin/guilds/{guild_id}/settings", self._handle_admin_get_settings)
        app.router.add_put("/admin/guilds/{guild_id}/settings", self._handle_admin_put_settings)
        app.router.add_get("/admin/guilds/{guild_id}/sounds", self._handle_admin_sounds)
        app.router.add_get("/admin/guilds/{guild_id}/trash", self._handle_admin_trash)
        app.router.add_post("/admin/guilds/{guild_id}/trash/{sound_id}/restore", self._handle_admin_restore)
        app.router.add_get("/admin/guilds/{guild_id}/stats", self._handle_admin_stats)
        app.router.add_get("/admin/guilds/{guild_id}/audit", self._handle_admin_audit)
        app.router.add_post("/admin/guilds/{guild_id}/blocked", self._handle_admin_blocked)
        app.router.add_get("/oauth/client-id", self._handle_oauth_client_id)
        app.router.add_post("/oauth/exchange", self._handle_oauth_exchange)
        app.router.add_post("/oauth/logout", self._handle_oauth_logout)

        if WEB_APP_ENABLED:
            web_app = webapp.WebApp(WEB_DIR, PUBLIC_URL, WEB_REDIRECT_URIS, self.oauth_client_id, SERVER_VERSION, SERVER_FEATURES,
                                    activity=WEB_ACTIVITY_ENABLED, invite_url=self.web_invite_url)
            if web_app.load():
                self.web_app = web_app
                web_app.add_routes(app.router)

        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, HTTP_HOST, HTTP_PORT)
        await site.start()
        log.info("Serveur HTTP prêt sur %s:%s (catalogue + ordres de lecture WaseBoard).", HTTP_HOST, HTTP_PORT)
        if not SHARED_SECRET:
            log.warning("Aucun shared_secret configuré : les routes de l'API ne sont PAS protégées par le jeton partagé. "
                        "Renseignez-en un (32 caractères aléatoires ou plus) avant d'exposer ce serveur.")
        if self.pcm_cache.enabled:
            asyncio.create_task(self._prewarm_pcm_cache())

    @staticmethod
    def _pcm_key(sound_id: str, file_path: Path) -> Optional[tuple]:
        try:
            st = file_path.stat()
        except OSError:
            return None
        return (sound_id, st.st_mtime_ns, st.st_size)

    def _schedule_pcm_load(self, key: tuple, file_path: Path) -> None:
        """Décode ce son en arrière-plan pour que les prochaines lectures démarrent instantanément."""
        if not self.pcm_cache.begin_load(key):
            return

        async def load() -> None:
            data = None
            try:
                data = await asyncio.get_running_loop().run_in_executor(
                    self._pcm_executor, decode_to_pcm, str(file_path), self.pcm_cache.max_item_bytes)
            except Exception:
                log.exception("Décodage en mémoire impossible : %s", file_path.name)
            finally:
                self.pcm_cache.end_load(key, data)

        asyncio.create_task(load())

    async def _prewarm_pcm_cache(self) -> None:
        """Au démarrage : décode les sons du catalogue (les plus récents d'abord) tant que le cache n'est pas plein."""
        try:
            for entry in reversed(load_catalog()):
                if self.pcm_cache.used_bytes > self.pcm_cache.max_bytes * 0.9:
                    break
                path = SOUNDS_DIR / f"{entry['id']}{entry['extension']}"
                key = self._pcm_key(entry["id"], path)
                if key is None or not self.pcm_cache.begin_load(key):
                    continue
                data = None
                try:
                    data = await asyncio.get_running_loop().run_in_executor(
                        self._pcm_executor, decode_to_pcm, str(path), self.pcm_cache.max_item_bytes)
                except Exception:
                    log.exception("Préchauffage : décodage impossible (%s)", path.name)
                finally:
                    self.pcm_cache.end_load(key, data)
            log.info("Sons préchargés en mémoire : %.0f Mo sur %.0f Mo.",
                     self.pcm_cache.used_bytes / 1048576, self.pcm_cache.max_bytes / 1048576)
        except Exception:
            log.exception("Préchauffage du cache audio interrompu")

    def _make_play_source(self, sound_id: str, entry: dict, file_path: Path, volume: float):
        """La source audio d'un clic : le son déjà décodé en mémoire s'il y est (démarrage instantané), sinon ffmpeg en
        flux comme avant — et on le décode en arrière-plan pour la fois suivante. Renvoie (source, "mémoire"|"ffmpeg")."""
        key = self._pcm_key(sound_id, file_path) if self.pcm_cache.enabled else None
        buffer = self.pcm_cache.get(key) if key is not None else None
        if buffer is not None:
            start, end = entry.get("trim_start_ms"), entry.get("trim_end_ms")
            trimmed = isinstance(start, int) and isinstance(end, int) and end > start
            source = PCMBufferSource(buffer, start if trimmed else None, end if trimmed else None)
            kind = "mémoire"
        else:
            before_options, options = trim_ffmpeg_options(entry)
            source = discord.FFmpegPCMAudio(str(file_path), before_options=before_options, options=options)
            kind = "ffmpeg"
            if key is not None:
                self._schedule_pcm_load(key, file_path)
        return discord.PCMVolumeTransformer(source, volume=min(max(volume, 0.0), 2.0)), kind

    @staticmethod
    def _effect_stream_options(entry: dict, audio_filter: str):
        """(before_options, options) de FFmpegPCMAudio pour un son AVEC effets : la portion gardée est découpée à l'ENTRÉE
        (-ss/-t avant -i), de sorte que les filtres — dont la vitesse, qui change la durée — s'appliquent à la bonne portion."""
        start, end = entry.get("trim_start_ms"), entry.get("trim_end_ms")
        trimmed = isinstance(start, int) and isinstance(end, int) and end > start
        before = f"-ss {start / 1000:.3f} -t {(end - start) / 1000:.3f}" if trimmed else None
        return before, f'-af "{audio_filter}"'

    async def _build_play_source(self, sound_id: str, entry: dict, file_path: Path, volume: float, *,
                                 loop: bool = False, effects_: Optional[dict] = None):
        """La source audio d'une lecture : le chemin rapide d'un clic simple (_make_play_source : mémoire, sinon ffmpeg),
        avec effets (ffmpeg et filtres, sans passer par le cache), ou en boucle (son décodé en mémoire qui recommence sans
        coupure, durée plafonnée par loop_max_minutes). Lève ValueError, avec un message lisible, si la boucle est impossible."""
        audio_filter = effects.build_audio_filter(effects_)
        if loop:
            return await self._build_loop_source(sound_id, entry, file_path, volume, audio_filter)
        if audio_filter:
            before, options = self._effect_stream_options(entry, audio_filter)
            source = discord.FFmpegPCMAudio(str(file_path), before_options=before, options=options)
            return discord.PCMVolumeTransformer(source, volume=volume), "effets"
        return self._make_play_source(sound_id, entry, file_path, volume)

    async def _build_loop_source(self, sound_id: str, entry: dict, file_path: Path, volume: float, audio_filter: Optional[str]):
        start, end = entry.get("trim_start_ms"), entry.get("trim_end_ms")
        trimmed = isinstance(start, int) and isinstance(end, int) and end > start

        buffer, slice_start, slice_end = None, None, None
        if not audio_filter:
            key = self._pcm_key(sound_id, file_path) if self.pcm_cache.enabled else None
            buffer = self.pcm_cache.get(key) if key is not None else None
            if buffer is not None and trimmed:
                slice_start, slice_end = start, end
        if buffer is None:
            # Pas en mémoire (ou avec effets) : décodage maintenant, dans le fil d'arrière-plan, plafonné à LOOP_MAX_BYTES.
            buffer = await asyncio.get_running_loop().run_in_executor(
                self._pcm_executor,
                lambda: decode_to_pcm(str(file_path), LOOP_MAX_BYTES, start_ms=start if trimmed else None,
                                      end_ms=end if trimmed else None, audio_filter=audio_filter))
            if buffer is None:
                raise ValueError("Ce son est trop long (ou illisible) pour tourner en boucle.")

        source = PCMBufferSource(buffer, slice_start, slice_end, loop=True, max_frames=round(LOOP_MAX_MINUTES * 60 * 50))
        return discord.PCMVolumeTransformer(source, volume=volume), "boucle"

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health : 200 quand le bot est connecté à Discord, 503 tant qu'il démarre (ou s'il est déconnecté).
        Volontairement sans authentification et sans aucun détail (ni nom de serveur ni compteur) : elle sert à
        un HEALTHCHECK Docker, à Caddy ou à une sonde externe."""
        ready = self.is_ready()
        return web.json_response({"status": "ok" if ready else "starting"}, status=200 if ready else 503,
                                 headers={"Cache-Control": "no-store"})

    def _check_secret(self, request: web.Request) -> bool:
        """Le secret partagé de l'instance (application Windows) : vrai s'il est présenté, ou si l'instance n'en a pas."""
        if not SHARED_SECRET:
            return True
        sent = request.headers.get("X-WaseBoard-Token", "")
        # Comparaison à temps constant (pas de fuite du secret par mesure de durée) ; en octets pour accepter tout texte.
        return hmac.compare_digest(sent.encode("utf-8"), SHARED_SECRET.encode("utf-8"))

    def _check_auth(self, request: web.Request) -> bool:
        """Première barrière de chaque route : le secret partagé, OU un appel de la page web (/app). Une page publique ne peut
        pas garder de secret : elle s'authentifie par la session Discord obtenue par OAuth2, présentée en `Authorization:
        Bearer` (la route vérifie ensuite que cette session est valide). On exige ici qu'elle existe et que l'origine
        éventuelle de l'appel soit celle du site."""
        if self._check_secret(request):
            return True
        if self.web_app is None:
            return False
        origin = request.headers.get("Origin")
        if not self.web_app.origin_allowed(origin):
            self.web_app.note_refused_origin(origin)
            return False
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and auth[len("Bearer "):].strip() in self.oauth_sessions

    async def _handle_connect_page(self, request: web.Request) -> web.Response:
        """GET /connect/{code} : page ouverte depuis le bouton Discord (donc SANS en-tête d'authentification, par
        conception). Elle ne livre le lien de connexion — adresse + secret partagé — qu'avec un code valide, émis
        à un membre qui a cliqué sur le bouton ; sans code valide, elle ne révèle rien."""
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
        code = request.match_info["code"]
        language = messages.pick_language(request.headers.get("Accept-Language"))
        if not (PUBLIC_URL and SHARED_SECRET and invite_code_is_valid(code)):
            return web.Response(text=render_connect_page(None, language=language), content_type="text/html", status=404, headers=headers)

        nonce = secrets.token_urlsafe(12)
        headers["Content-Security-Policy"] = (
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'"
        )
        return web.Response(text=render_connect_page(build_connect_link(), nonce, language), content_type="text/html", headers=headers)

    # ---------- Catalogue de sons ----------

    async def _handle_list_sounds(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        user_id = session["user_id"]
        visible, perms = await self.user_access(user_id)
        # is_mine / can_edit sont calculés pour CE demandeur : le client s'en sert uniquement
        # pour masquer des actions, le serveur les re-vérifie à chaque requête de modification.
        sounds = [
            {**entry, "is_mine": is_sound_mine(entry, user_id), "can_edit": can_edit_sound(entry, user_id, perms)}
            for entry in self.catalog_visible_to(load_catalog(), visible)
        ]
        return web.json_response({"sounds": sounds})

    async def _handle_get_file(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        sound_id = request.match_info["id"]
        entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
        if entry is None:
            return web.json_response({"error": "son introuvable"}, status=404)

        visible = await self.resolve_visible_guild_ids(int(session["user_id"]))
        if not self.sound_visible_to(entry, visible):
            return web.json_response({"error": "son introuvable"}, status=404)

        file_path = SOUNDS_DIR / f"{sound_id}{entry['extension']}"
        if not file_path.exists():
            return web.json_response({"error": "fichier manquant sur le serveur"}, status=404)

        return web.FileResponse(file_path)

    async def _handle_upload_sound(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.post()
            name = str(data.get("name", "")).strip()
            file_field = data.get("file")
            raw_guild_id = str(data.get("guild_id", "")).strip()

            if not name or file_field is None or not raw_guild_id:
                return web.json_response({"error": "champs 'name', 'file' et 'guild_id' requis"}, status=400)

            # Découpe non destructive (facultative) : le fichier stocké est l'original complet, la portion
            # gardée n'est qu'une paire de repères appliquée à la lecture. L'emoji se choisit dès l'ajout.
            try:
                trim = parse_trim(data.get("trim_start_ms"), data.get("trim_end_ms"))
            except ValueError as ex:
                return web.json_response({"error": str(ex)}, status=400)
            emoji = str(data.get("emoji", "")).strip()[:32]

            user_id = session["user_id"]
            memberships = await self.find_memberships(int(user_id))
            if not any(str(g.id) == raw_guild_id for g, _ in memberships):
                return web.json_response({"error": "vous n'êtes pas membre de ce serveur Discord"}, status=403)

            settings = get_guild_settings(raw_guild_id)
            if not compute_permissions(memberships)[raw_guild_id]["can_upload"]:
                message = ("Les ajouts de sons sont réservés aux administrateurs de ce serveur."
                           if settings["upload_admins_only"]
                           else "Un administrateur de ce serveur vous a retiré le droit d'ajouter des sons.")
                return web.json_response({"error": message}, status=403)

            max_sounds = settings["max_sounds"]
            if max_sounds > 0 and sum(1 for s in load_catalog() if s.get("guild_id") == raw_guild_id) >= max_sounds:
                return web.json_response({"error": f"Ce serveur a atteint sa limite de {max_sounds} sons."}, status=403)

            original_filename = getattr(file_field, "filename", "") or ""
            extension = Path(original_filename).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                return web.json_response({"error": f"extension non supportée : {extension}"}, status=400)

            # Hash du contenu (calculé ici, jamais fourni par le client) : sert de base à la
            # détection de doublons côté client, qui compare son fichier source (l'original, avant
            # toute découpe) au hash de chaque son déjà présent dans le catalogue avant d'uploader.
            file_bytes = file_field.file.read()
            content_hash = hashlib.sha256(file_bytes).hexdigest()

            max_file_mb = settings["max_file_mb"]
            if max_file_mb > 0 and len(file_bytes) > max_file_mb * 1024 * 1024:
                return web.json_response({"error": f"Fichier trop volumineux (maximum {max_file_mb} Mo sur ce serveur)."}, status=413)

            max_total_mb = settings["max_total_mb"]
            if max_total_mb > 0:
                _, used = await asyncio.to_thread(guild_used_bytes, raw_guild_id)
                if used + len(file_bytes) > max_total_mb * 1024 * 1024:
                    return web.json_response({
                        "error": f"Espace insuffisant sur ce serveur : {used / 1048576:.0f} Mo utilisés sur {max_total_mb} Mo, "
                                 f"et ce fichier fait {len(file_bytes) / 1048576:.1f} Mo."
                    }, status=413)

            sound_id = uuid.uuid4().hex
            destination = SOUNDS_DIR / f"{sound_id}{extension}"
            with open(destination, "wb") as out_file:
                out_file.write(file_bytes)

            # La limite de durée porte sur ce qui sera réellement joué : la portion gardée si le son est
            # découpé, sinon le fichier entier (mesuré avec ffprobe).
            max_duration = settings["max_duration_s"]
            if max_duration > 0:
                duration = (trim[1] - trim[0]) / 1000 if trim else await probe_duration_seconds(destination)
                if duration is not None and duration > max_duration:
                    destination.unlink(missing_ok=True)
                    return web.json_response(
                        {"error": f"Son trop long ({duration:.0f} s, maximum {max_duration} s sur ce serveur)."},
                        status=413)

            # Catalogue chargé APRÈS tous les await : aucun autre handler ne peut s'intercaler
            # entre ce chargement et la réécriture.
            catalog = load_catalog()
            entry = {
                "id": sound_id, "name": name, "extension": extension, "hash": content_hash,
                "guild_id": raw_guild_id, "uploaded_by": str(user_id), "uploaded_at": int(time.time()),
            }
            if trim:
                entry["trim_start_ms"], entry["trim_end_ms"] = trim
            if emoji:
                entry["emoji"] = emoji
            catalog.append(entry)
            save_catalog(catalog)
            record_audit(raw_guild_id, user_id, session.get("username"), "upload", sound_id, name)

            log.info("Son ajouté : %s (%s)", name, sound_id)
            return web.json_response({**entry, "is_mine": True, "can_edit": True})
        except Exception as ex:
            log.exception("Échec de l'upload")
            return web.json_response({"error": str(ex)}, status=500)

    async def _handle_update_sound(self, request: web.Request) -> web.Response:
        """PATCH /sounds/{id} : renomme, change l'emoji et/ou la découpe d'un son (partagés entre
        tous les utilisateurs, contrairement aux favoris/categories/volume/raccourcis, locaux)."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        sound_id = request.match_info["id"]
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        user_id = session["user_id"]
        visible, perms = await self.user_access(user_id)

        # Catalogue chargé APRÈS le dernier await : sinon un upload concurrent, terminé pendant
        # la résolution des membres Discord ci-dessus, serait écrasé par la réécriture plus bas.
        catalog = load_catalog()
        entry = next((s for s in catalog if s["id"] == sound_id), None)
        if entry is None or not self.sound_visible_to(entry, visible):
            return web.json_response({"error": "son introuvable"}, status=404)

        if not can_edit_sound(entry, user_id, perms):
            return web.json_response(
                {"error": "Seuls l'auteur du son ou un administrateur du serveur peuvent le modifier."}, status=403)

        has_trim = "trim_start_ms" in data or "trim_end_ms" in data
        if "name" not in data and "emoji" not in data and not has_trim:
            return web.json_response({"error": "'name', 'emoji' ou une découpe requis"}, status=400)

        # Tout est validé AVANT de modifier l'entrée : un refus ne doit pas laisser de changement partiel.
        new_name = None
        if "name" in data:
            new_name = str(data.get("name", "")).strip()
            if not new_name:
                return web.json_response({"error": "'name' ne peut pas etre vide"}, status=400)

        new_trim = None
        if has_trim:
            try:
                new_trim = parse_trim(data.get("trim_start_ms"), data.get("trim_end_ms"))
            except ValueError as ex:
                return web.json_response({"error": str(ex)}, status=400)

        old_name = entry["name"]
        old_trim = [entry.get("trim_start_ms"), entry.get("trim_end_ms")]
        if new_name is not None:
            entry["name"] = new_name
        if "emoji" in data:
            entry["emoji"] = str(data.get("emoji") or "").strip()
        if has_trim:
            # Les deux nuls : on retire la découpe (le son entier est toujours là, rien n'est perdu).
            if new_trim is None:
                entry.pop("trim_start_ms", None)
                entry.pop("trim_end_ms", None)
            else:
                entry["trim_start_ms"], entry["trim_end_ms"] = new_trim

        save_catalog(catalog)
        if new_name is not None:
            record_audit(entry.get("guild_id"), user_id, session.get("username"), "rename",
                         sound_id, entry["name"], {"from": old_name, "to": entry["name"]})
        if "emoji" in data:
            record_audit(entry.get("guild_id"), user_id, session.get("username"), "emoji",
                         sound_id, entry["name"], {"emoji": entry["emoji"]})
        if has_trim:
            record_audit(entry.get("guild_id"), user_id, session.get("username"), "trim",
                         sound_id, entry["name"],
                         {"from": old_trim if old_trim[0] is not None else None,
                          "to": list(new_trim) if new_trim else None})
        return web.json_response({**entry, "is_mine": is_sound_mine(entry, user_id), "can_edit": True})

    async def _handle_replace_sound_file(self, request: web.Request) -> web.Response:
        """PUT /sounds/{id}/file : remplace le fichier audio d'un son existant EN GARDANT son identité (id, nom, emoji,
        catégories partagées, favoris/volumes locaux des utilisateurs). La découpe est retirée : elle visait l'ancien
        fichier. Mêmes droits que la modification (auteur ou admin), mêmes plafonds de taille/durée/espace que l'ajout.
        L'ancien fichier n'est PAS conservé (le client demande confirmation avant)."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        sound_id = request.match_info["id"]
        user_id = session["user_id"]
        temp_path: Optional[Path] = None
        try:
            data = await request.post()
            file_field = data.get("file")
            if file_field is None:
                return web.json_response({"error": "champ 'file' requis"}, status=400)

            visible, perms = await self.user_access(user_id)
            entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
            if entry is None or not self.sound_visible_to(entry, visible):
                return web.json_response({"error": "son introuvable"}, status=404)
            if not can_edit_sound(entry, user_id, perms):
                return web.json_response(
                    {"error": "Seuls l'auteur du son ou un administrateur du serveur peuvent remplacer son fichier."}, status=403)
            # Remplacer un fichier revient à en ajouter un : un membre bloqué, ou sur un serveur dont les ajouts sont
            # réservés aux admins, ne doit pas contourner cette règle via son propre son.
            if not perms[str(entry["guild_id"])]["can_upload"]:
                return web.json_response({"error": "Vous n'avez pas le droit d'ajouter des sons sur ce serveur."}, status=403)

            extension = Path(getattr(file_field, "filename", "") or "").suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                return web.json_response({"error": f"extension non supportée : {extension}"}, status=400)

            file_bytes = file_field.file.read()
            new_hash = hashlib.sha256(file_bytes).hexdigest()
            if new_hash == entry.get("hash"):
                # Même contenu : rien à remplacer (et surtout pas de découpe à perdre pour rien).
                return web.json_response({**entry, "is_mine": is_sound_mine(entry, user_id), "can_edit": True, "unchanged": True})

            # Les plafonds sont ceux de la guilde propriétaire du son (comme à l'ajout).
            settings = get_guild_settings(entry.get("guild_id"))
            max_file_mb = settings["max_file_mb"]
            if max_file_mb > 0 and len(file_bytes) > max_file_mb * 1024 * 1024:
                return web.json_response({"error": f"Fichier trop volumineux (maximum {max_file_mb} Mo sur ce serveur)."}, status=413)

            old_extension = entry["extension"]
            try:
                old_size = (SOUNDS_DIR / f"{sound_id}{old_extension}").stat().st_size
            except OSError:
                old_size = 0
            max_total_mb = settings["max_total_mb"]
            if max_total_mb > 0 and entry.get("guild_id"):
                _, used = await asyncio.to_thread(guild_used_bytes, entry["guild_id"])
                # L'ancien fichier disparaît : seul le surplus compte contre l'espace du serveur.
                if used - old_size + len(file_bytes) > max_total_mb * 1024 * 1024:
                    return web.json_response({
                        "error": f"Espace insuffisant sur ce serveur : {used / 1048576:.0f} Mo utilisés sur {max_total_mb} Mo, "
                                 f"et ce fichier fait {len(file_bytes) / 1048576:.1f} Mo (l'ancien fichier de {old_size / 1048576:.1f} Mo "
                                 f"est libéré par le remplacement)."
                    }, status=413)

            # Écrit à côté, jamais par-dessus : un son en cours de lecture continue sur l'ancien fichier, et un refus
            # (durée) laisse tout intact.
            temp_path = SOUNDS_DIR / f"{sound_id}.replacing{extension}"
            with open(temp_path, "wb") as out_file:
                out_file.write(file_bytes)

            max_duration = settings["max_duration_s"]
            if max_duration > 0:
                duration = await probe_duration_seconds(temp_path)
                if duration is not None and duration > max_duration:
                    return web.json_response(
                        {"error": f"Son trop long ({duration:.0f} s, maximum {max_duration} s sur ce serveur)."}, status=413)

            # Catalogue rechargé APRÈS tous les await (voir les autres handlers) ; le son a pu être supprimé entre-temps.
            catalog = load_catalog()
            entry = next((s for s in catalog if s["id"] == sound_id), None)
            if entry is None:
                return web.json_response({"error": "son introuvable"}, status=404)

            old_extension, old_hash = entry["extension"], entry.get("hash")
            new_path = SOUNDS_DIR / f"{sound_id}{extension}"
            os.replace(temp_path, new_path)
            temp_path = None

            entry["extension"] = extension
            entry["hash"] = new_hash
            entry["replaced_at"] = int(time.time())
            entry.pop("trim_start_ms", None)
            entry.pop("trim_end_ms", None)
            try:
                save_catalog(catalog)
            except Exception:
                if old_extension != extension:
                    new_path.unlink(missing_ok=True)
                raise
            if old_extension != extension:
                (SOUNDS_DIR / f"{sound_id}{old_extension}").unlink(missing_ok=True)

            record_audit(entry.get("guild_id"), user_id, session.get("username"), "replace", sound_id, entry["name"],
                         {"from_hash": (old_hash or "")[:12], "to_hash": new_hash[:12],
                          "bytes": len(file_bytes), "extension": extension})
            log.info("Fichier du son remplacé : %s (%s)", entry["name"], sound_id)
            return web.json_response({**entry, "is_mine": is_sound_mine(entry, user_id), "can_edit": True})
        except Exception as ex:
            log.exception("Échec du remplacement de fichier")
            return web.json_response({"error": str(ex)}, status=500)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    async def _handle_delete_sound(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        sound_id = request.match_info["id"]
        user_id = session["user_id"]
        visible, perms = await self.user_access(user_id)

        # Pas d'await entre ce chargement et trash_sound (qui relit lui-même le catalogue).
        entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
        if entry is None or not self.sound_visible_to(entry, visible):
            return web.json_response({"error": "son introuvable"}, status=404)

        if not can_edit_sound(entry, user_id, perms):
            return web.json_response(
                {"error": "Seuls l'auteur du son ou un administrateur du serveur peuvent le supprimer."}, status=403)

        # Corbeille plutôt que suppression définitive : un admin peut restaurer le son.
        trash_sound(sound_id, user_id)
        record_audit(entry.get("guild_id"), user_id, session.get("username"), "delete", sound_id, entry["name"])

        return web.json_response({"status": "ok"})

    # ---------- Serveurs Discord / statut ----------

    async def _handle_my_guilds(self, request: web.Request) -> web.Response:
        """Liste TOUS les serveurs Discord dont cet utilisateur est membre — chacun a
        automatiquement sa propre catégorie partagée côté client, sans rien à créer."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        memberships = await self.find_memberships(int(session["user_id"]))
        perms = compute_permissions(memberships)
        return web.json_response({
            "guilds": [
                {
                    "id": str(g.id), "name": g.name, "icon_url": str(g.icon.url) if g.icon else None,
                    "is_admin": perms[str(g.id)]["is_admin"], "can_upload": perms[str(g.id)]["can_upload"],
                }
                for g, _ in memberships
            ]
        })

    async def _handle_oauth_client_id(self, request: web.Request) -> web.Response:
        """Renvoie l'identifiant client OAuth2 Discord de cette instance — pas un secret,
        mais propre à chaque déploiement auto-hébergé, donc le client ne peut pas le
        connaître à l'avance."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        client_id = self.oauth_client_id()
        if not client_id:
            return web.json_response({"error": "identifiant client OAuth2 indisponible côté serveur"}, status=500)
        return web.json_response({"client_id": client_id})

    async def _handle_oauth_logout(self, request: web.Request) -> web.Response:
        """POST /oauth/logout : supprime la session de l'appelant (jetons Discord compris) du serveur. Idempotent :
        une session déjà absente répond aussi « ok »."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[len("Bearer "):].strip()
            session = self.oauth_sessions.pop(token, None)
            if session is not None:
                save_oauth_sessions(self.oauth_sessions)
                log.info("Session OAuth2 fermée par %s (%s).", session.get("user_id"), session.get("username"))
        return web.json_response({"status": "ok"})

    async def _handle_oauth_exchange(self, request: web.Request) -> web.Response:
        """Échange un code d'autorisation Discord contre une session WaseBoard. Le
        client_secret Discord reste ici, jamais transmis au client. Deux appelants : l'application Windows (secret partagé,
        retour en boucle locale) et la page web /app (origine du site, retour sur /app/callback) — la page n'a pas encore de
        session, c'est justement ce qu'elle vient chercher."""
        secret_ok = self._check_secret(request)
        origin = request.headers.get("Origin")
        web_ok = self.web_app is not None and origin is not None and self.web_app.origin_allowed(origin)
        # Activité Discord : le code est obtenu DANS Discord (SDK) et échangé sans adresse de retour. Le proxy de Discord peut
        # réécrire ou retirer l'en-tête Origin : ce mode est donc reconnu à l'absence d'adresse de retour, pas à l'origine
        # (celle-ci n'est de toute façon qu'une barrière légère : ce qui compte, c'est un code Discord valide pour CETTE application).
        activity_ok = self.web_app is not None and self.web_app.enabled and self.web_app.activity
        if not (secret_ok or web_ok or activity_ok):
            if self.web_app is not None:
                self.web_app.note_refused_origin(origin)
            return web.json_response({"error": "unauthorized"}, status=401)
        if not self.oauth_client_id() or not OAUTH2_CLIENT_SECRET:
            return web.json_response({"error": "OAuth2 non configuré côté serveur"}, status=500)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        if not isinstance(data, dict):
            return web.json_response({"error": "corps JSON invalide"}, status=400)
        code = data.get("code")
        redirect_uri = data.get("redirect_uri") or None
        activity_exchange = redirect_uri is None and activity_ok     # code obtenu dans Discord (Activité)
        if redirect_uri is not None and not (secret_ok or web_ok):
            # Avec une adresse de retour (application Windows ou page web), l'appelant doit toujours être reconnu.
            if self.web_app is not None:
                self.web_app.note_refused_origin(origin)
            return web.json_response({"error": "unauthorized"}, status=401)
        if not code or (redirect_uri is None and not activity_exchange):
            return web.json_response({"error": "'code' et 'redirect_uri' requis"}, status=400)
        # Défense en profondeur : n'accepte que les adresses de retour prévues — la boucle locale de l'application Windows,
        # ou celle de la page web de CE serveur — pour ne pas servir de proxy générique d'échange de code vers un
        # redirect_uri arbitraire. Sans le secret partagé, seule la page web est acceptée.
        if redirect_uri is not None:
            loopback = isinstance(redirect_uri, str) and redirect_uri.startswith(("http://127.0.0.1:", "http://localhost:"))
            web_callback = self.web_app is not None and redirect_uri in self.web_app.redirect_uris()
            if not (web_callback or (secret_ok and loopback)):
                return web.json_response({"error": "redirect_uri non autorisé"}, status=400)

        token_request = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.oauth_client_id(),
            "client_secret": OAUTH2_CLIENT_SECRET,
        }
        if redirect_uri is not None:
            token_request["redirect_uri"] = redirect_uri
        try:
            token_data = await self._discord_token_request(token_request)
        except Exception as ex:
            log.exception("Échec de l'échange de code OAuth2 Discord")
            return web.json_response({"error": f"Échange avec Discord échoué : {ex}"}, status=502)

        session = {
            "discord_access_token": token_data["access_token"],
            "discord_refresh_token": token_data["refresh_token"],
            "discord_token_expires_at": time.time() + token_data["expires_in"],
            "created_at": time.time(),
            "last_used_at": time.time(),
        }
        try:
            await self._refresh_identity_from_discord(session)
        except Exception as ex:
            log.exception("Échec de /users/@me après échange OAuth2")
            return web.json_response({"error": f"Identité Discord introuvable : {ex}"}, status=502)

        session_token = secrets.token_urlsafe(32)
        self.oauth_sessions[session_token] = session
        save_oauth_sessions(self.oauth_sessions)
        log.info("Nouvelle session OAuth2 pour %s (%s)", session["user_id"], session["username"])

        response = {
            "session_token": session_token,
            "user_id": session["user_id"],
            "username": session["username"],
            "avatar_url": session["avatar_url"],
        }
        if activity_exchange:
            # Le SDK de Discord demande ce jeton (portée « identify » seulement, celui de la personne elle-même) pour
            # « authenticate ». Il n'est renvoyé qu'à ce mode d'échange ; le serveur garde le sien de son côté.
            response["discord_access_token"] = token_data["access_token"]
        return web.json_response(response)

    async def _handle_status(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        strict = request.query.get("strict") == "1"
        # Non-strict : utilisable sans session (secret partagé seul) — nécessaire pour
        # "Tester la connexion" dans l'onboarding, AVANT toute connexion Discord. Strict
        # (indicateur de présence vocale fiable, affiché en continu dans la barre latérale)
        # exige en revanche une session valide.
        session, err = await self._require_session(request, required=strict)
        if err is not None:
            return err

        user_id = session["user_id"] if session else None
        guild_id = self.resolve_guild_id(request.query.get("guild_id"), user_id, strict=strict)
        vc = self.voice_clients_map.get(guild_id) if guild_id is not None else None
        connected = vc is not None and vc.is_connected()
        guild = self.get_guild(guild_id) if guild_id is not None else None

        channel_members = []
        if connected:
            channel_members = [
                {"user_id": str(m.id), "username": m.display_name, "avatar_url": str(m.display_avatar.url)}
                for m in vc.channel.members if not m.bot
            ]

        return web.json_response({
            "connected": connected,
            "channel": vc.channel.name if connected else None,
            "guild_id": str(guild_id) if guild_id is not None else None,
            "guild_name": guild.name if guild else None,
            "connected_guild_count": len(self.voice_clients_map),
            "channel_members": channel_members,
            "features": SERVER_FEATURES,
            "server_version": SERVER_VERSION,
            "uptime_s": int(time.time() - STARTED_AT),
        })

    # ---------- Activité en temps réel & présence ----------

    async def _handle_activity(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        # Sondé toutes les 300ms, y compris AVANT toute connexion Discord : reste utilisable
        # sans session (comportement inchangé dans ce cas — aucune présence enregistrée).
        session, err = await self._require_session(request, required=False)
        if err is not None:
            return err

        guild_id = None
        if session is not None:
            user_id = session["user_id"]
            member = self.find_member(int(user_id))
            self.record_presence(
                user_id,
                member.display_name if member else "?",
                str(member.display_avatar.url) if member else ""
            )
            # strict=True (comme /status) : on ne veut PAS du filet de sécurité "un seul salon
            # connecté ? on suppose que c'est le bon", qui montrerait l'activité d'un salon où
            # l'appelant n'est en réalité pas présent.
            guild_id = self.resolve_guild_id(request.query.get("guild_id"), user_id, strict=True)

        return web.json_response({
            "activity": self.get_active_snapshot(guild_id),
            "online": self.get_online_snapshot(),
        })

    # ---------- Catégorie partagée automatique par serveur Discord ----------
    # Une seule catégorie par guild, sans nom à choisir ni bouton de création : son existence
    # découle directement de la présence du serveur Discord, pas d'une action manuelle.

    async def _handle_get_shared_categories(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        guild_id = request.query.get("guild_id")
        if not guild_id:
            return web.json_response({"error": "guild_id requis"}, status=400)

        guilds = await self.find_all_guilds_for_user(int(session["user_id"]))
        if not any(str(g.id) == guild_id for g in guilds):
            return web.json_response({"sound_ids": []}, status=403)

        shared = load_shared_categories()
        return web.json_response({"sound_ids": shared.get(guild_id, [])})

    async def _handle_shared_category_sound(self, request: web.Request) -> web.Response:
        """Ajoute ou retire un son de la catégorie partagée d'un serveur Discord. Body: {guild_id, sound_id, action: "add"|"remove"}."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        guild_id = str(data.get("guild_id", ""))
        sound_id = str(data.get("sound_id", ""))
        action = data.get("action", "add")
        user_id = session["user_id"]
        if action not in ("add", "remove"):
            return web.json_response({"error": "action invalide"}, status=400)

        visible, perms = await self.user_access(user_id)
        if guild_id not in visible:
            return web.json_response({"error": "vous n'êtes pas membre de ce serveur Discord"}, status=403)

        entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
        if entry is None or not self.sound_visible_to(entry, visible):
            return web.json_response({"error": "son introuvable"}, status=404)

        # Même règle pour ajouter et retirer : un membre ordinaire ne touche qu'à ses propres sons.
        if not can_share_sound(entry, user_id, guild_id, perms):
            return web.json_response(
                {"error": "Seuls l'auteur du son ou un administrateur de ce serveur peuvent modifier sa catégorie partagée."},
                status=403)

        shared = load_shared_categories()
        sound_ids = shared.setdefault(guild_id, [])

        if action == "add" and sound_id not in sound_ids:
            sound_ids.append(sound_id)
            record_audit(guild_id, user_id, session.get("username"), "share_add", sound_id, entry["name"])
        elif action == "remove" and sound_id in sound_ids:
            sound_ids.remove(sound_id)
            record_audit(guild_id, user_id, session.get("username"), "share_remove", sound_id, entry["name"])

        save_shared_categories(shared)
        return web.json_response({"sound_ids": sound_ids})

    # ---------- Préférences personnelles (favoris, couleurs, catégories perso, ordre) ----------

    @staticmethod
    def _preferences_error(error: str) -> web.Response:
        message = "trop d'éléments dans vos préférences (favoris, couleurs, catégories)" if error == "limit" else "préférences invalides"
        return web.json_response({"error": message}, status=400)

    async def _handle_get_preferences(self, request: web.Request) -> web.Response:
        """GET /me/preferences : la copie des préférences de la personne connectée ({rev, doc}), vide tant qu'aucun appareil n'en a envoyé."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        stored = load_user_preferences().get(str(session["user_id"]), {})
        return web.json_response({"rev": int(stored.get("rev") or 0), "doc": user_preferences_doc(stored)}, headers={"Cache-Control": "no-store"})

    async def _handle_sync_preferences(self, request: web.Request) -> web.Response:
        """POST /me/preferences/sync : Body {base, doc} — `doc` est l'état de l'appareil, `base` ce qu'il avait reçu du serveur la fois d'avant
        (null à la première synchronisation). Le serveur fusionne (preferences.merge), enregistre et renvoie {rev, doc} : le résultat, que
        l'appareil adopte. Appeler sans modification (doc = base) sert simplement à récupérer ce que les autres appareils ont changé."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "corps JSON invalide"}, status=400)
        mine, error = preferences.normalize(data.get("doc"))
        base, base_error = preferences.normalize(data.get("base"))
        for problem in (error, base_error):
            if problem is not None:
                return self._preferences_error(problem)

        rev, merged = sync_user_preferences(str(session["user_id"]), base, mine)
        return web.json_response({"rev": rev, "doc": merged}, headers={"Cache-Control": "no-store"})

    async def _handle_get_my_categories(self, request: web.Request) -> web.Response:
        """GET /me/categories (3.5.0, conservé pour les anciens clients) : catégories et ordre de la copie des préférences."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        stored = load_user_preferences().get(str(session["user_id"]), {})
        doc = user_preferences_doc(stored)
        return web.json_response({"categories": doc["categories"], "order": doc["order"], "updated_at": stored.get("updated_at")},
                                 headers={"Cache-Control": "no-store"})

    async def _handle_put_my_categories(self, request: web.Request) -> web.Response:
        """PUT /me/categories (3.5.0, conservé pour les anciens clients) : l'ancien client envoie ses catégories sans savoir ce que le serveur
        garde. Elles sont donc FUSIONNÉES (sans état de départ : rien n'est retiré) avec les préférences, pour ne pas effacer ce qui a été
        modifié depuis la page web."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "corps JSON invalide"}, status=400)
        mine, error = preferences.normalize({"categories": data.get("categories", {}), "order": data.get("order", [])})
        if error is not None:
            return self._preferences_error(error)

        _, merged = sync_user_preferences(str(session["user_id"]), None, mine)
        return web.json_response({"ok": True, "count": len(merged["categories"])})

    # ---------- Administration d'une guilde ----------

    async def _admin_context(self, request: web.Request):
        """Authentifie la requête et vérifie que l'appelant est admin de la guilde de l'URL
        (/admin/guilds/{guild_id}/...). Renvoie (session, guilde, None) ou (None, None, réponse d'erreur)."""
        if not self._check_auth(request):
            return None, None, web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return None, None, err

        guild_id = request.match_info["guild_id"]
        memberships = await self.find_memberships(int(session["user_id"]))
        match = next(((g, m) for g, m in memberships if str(g.id) == guild_id), None)
        if match is None or not is_guild_admin(match[0], match[1]):
            return None, None, web.json_response({"error": "Réservé aux administrateurs de ce serveur."}, status=403)
        return session, match[0], None

    @staticmethod
    def _member_display_name(guild: discord.Guild, user_id) -> Optional[str]:
        member = guild.get_member(int(user_id)) if user_id and str(user_id).isdigit() else None
        return member.display_name if member else None

    def _settings_payload(self, guild: discord.Guild) -> dict:
        settings = get_guild_settings(guild.id)
        roles = [
            {"id": str(r.id), "name": r.name}
            for r in sorted(guild.roles, key=lambda r: r.position, reverse=True)
            if not r.is_default() and not r.managed
        ]
        blocked = [{"user_id": uid, "username": self._member_display_name(guild, uid)} for uid in settings["blocked_uploaders"]]
        sound_count, used = guild_used_bytes(guild.id)
        return {
            "settings": settings, "roles": roles, "blocked": blocked,
            # Plafonds imposés par l'hébergeur (déjà appliqués aux valeurs de "settings") et consommation actuelle.
            "ceilings": dict(GUILD_LIMIT_CEILINGS),
            "usage": {"sounds": sound_count, "total_mb": round(used / 1048576, 1)},
        }

    async def _handle_admin_get_settings(self, request: web.Request) -> web.Response:
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err
        return web.json_response(self._settings_payload(guild))

    async def _handle_admin_put_settings(self, request: web.Request) -> web.Response:
        """Met à jour les réglages de la guilde. Seules les clés présentes dans le corps sont
        modifiées (les membres bloqués ont leur propre endpoint)."""
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        def is_count(value, maximum: int) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum

        limits = {"max_sounds": 100000, "max_file_mb": 1024, "max_duration_s": 3600, "max_total_mb": 1048576,
                  "play_rate_per_min": 600, "max_sources_per_guild": 50}
        settings = get_guild_settings(guild.id, clamp=False)  # valeurs brutes : les plafonds ne sont jamais écrits sur disque
        before = dict(settings)

        if "admin_role_id" in data:
            raw = data["admin_role_id"]
            if raw in (None, ""):
                settings["admin_role_id"] = None
            elif str(raw).isdigit() and guild.get_role(int(raw)) is not None:
                settings["admin_role_id"] = str(raw)
            else:
                return web.json_response({"error": "rôle introuvable sur ce serveur"}, status=400)

        if "upload_admins_only" in data:
            if not isinstance(data["upload_admins_only"], bool):
                return web.json_response({"error": "'upload_admins_only' doit être un booléen"}, status=400)
            settings["upload_admins_only"] = data["upload_admins_only"]

        for key, maximum in limits.items():
            if key in data:
                if not is_count(data[key], maximum):
                    return web.json_response({"error": f"'{key}' doit être un entier entre 0 et {maximum}"}, status=400)
                ceiling = GUILD_LIMIT_CEILINGS.get(key, 0)
                if ceiling > 0 and (data[key] == 0 or data[key] > ceiling):
                    return web.json_response({
                        "error": f"'{key}' est limité à {ceiling} par l'hébergeur de ce serveur WaseBoard "
                                 "(« illimité » n'est pas permis)."}, status=400)
                settings[key] = data[key]

        save_guild_settings(guild.id, settings)
        changed = {k: [before[k], settings[k]] for k in settings if before[k] != settings[k]}
        if changed:
            record_audit(guild.id, session["user_id"], session.get("username"), "settings", details=changed)
        return web.json_response(self._settings_payload(guild))

    async def _handle_admin_sounds(self, request: web.Request) -> web.Response:
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err

        plays = await asyncio.to_thread(count_plays_per_sound)
        sounds = []
        for entry in load_catalog():
            if entry.get("guild_id") != str(guild.id):
                continue
            file_path = SOUNDS_DIR / f"{entry['id']}{entry['extension']}"
            uploader = entry.get("uploaded_by")
            sounds.append({
                "id": entry["id"],
                "name": entry["name"],
                "emoji": entry.get("emoji"),
                "uploaded_by": uploader,
                "uploaded_by_name": self._member_display_name(guild, uploader),
                "uploaded_at": entry.get("uploaded_at"),
                "size": file_path.stat().st_size if file_path.exists() else None,
                "plays": plays.get(entry["id"], 0),
            })
        return web.json_response({"sounds": sounds})

    async def _handle_admin_trash(self, request: web.Request) -> web.Response:
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err

        now = time.time()
        items = []
        for record in load_trash():
            if record.get("guild_id") != str(guild.id):
                continue
            days_left = None
            if TRASH_RETENTION_DAYS > 0:
                days_left = max(0, int((record["deleted_at"] + TRASH_RETENTION_DAYS * 86400 - now) // 86400))
            items.append({
                "id": record["id"],
                "name": record["name"],
                "emoji": record.get("emoji"),
                "uploaded_by_name": self._member_display_name(guild, record.get("uploaded_by")),
                "deleted_by": record.get("deleted_by"),
                "deleted_by_name": self._member_display_name(guild, record.get("deleted_by")),
                "deleted_at": record["deleted_at"],
                "days_left": days_left,
            })
        items.sort(key=lambda i: i["deleted_at"], reverse=True)
        return web.json_response({"trash": items, "retention_days": TRASH_RETENTION_DAYS})

    async def _handle_admin_restore(self, request: web.Request) -> web.Response:
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err

        sound_id = request.match_info["sound_id"]
        record = next((t for t in load_trash() if t["id"] == sound_id and t.get("guild_id") == str(guild.id)), None)
        if record is None:
            return web.json_response({"error": "son introuvable dans la corbeille"}, status=404)

        entry = restore_sound(sound_id)
        if entry is None:
            return web.json_response({"error": "son introuvable dans la corbeille"}, status=404)
        record_audit(guild.id, session["user_id"], session.get("username"), "restore", sound_id, entry["name"])
        return web.json_response({"status": "ok"})

    async def _handle_admin_stats(self, request: web.Request) -> web.Response:
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err
        try:
            days = min(max(int(request.query.get("days", "30")), 1), 365)
        except ValueError:
            return web.json_response({"error": "'days' doit être un entier"}, status=400)

        catalog_by_id = {e["id"]: e for e in load_catalog()}
        stats = await asyncio.to_thread(aggregate_play_stats, str(guild.id), days, catalog_by_id)
        return web.json_response(stats)

    async def _handle_admin_audit(self, request: web.Request) -> web.Response:
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err
        try:
            limit = min(max(int(request.query.get("limit", "100")), 1), 500)
            before = float(request.query["before"]) if "before" in request.query else None
        except ValueError:
            return web.json_response({"error": "paramètres invalides"}, status=400)

        entries = await asyncio.to_thread(read_audit, str(guild.id), limit, before)
        return web.json_response({"entries": entries})

    async def _handle_admin_blocked(self, request: web.Request) -> web.Response:
        """Interdit (ou ré-autorise) un membre d'uploader sur cette guilde. Body: {user_id, blocked}."""
        session, guild, err = await self._admin_context(request)
        if err is not None:
            return err
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        target_id = str(data.get("user_id", ""))
        blocked = data.get("blocked")
        if not target_id.isdigit() or not isinstance(blocked, bool):
            return web.json_response({"error": "'user_id' (numérique) et 'blocked' (booléen) requis"}, status=400)

        member = guild.get_member(int(target_id))
        if blocked and member is not None and is_guild_admin(guild, member):
            return web.json_response({"error": "Impossible de bloquer un administrateur."}, status=400)

        settings = get_guild_settings(guild.id)
        if blocked and target_id not in settings["blocked_uploaders"]:
            settings["blocked_uploaders"].append(target_id)
        elif not blocked and target_id in settings["blocked_uploaders"]:
            settings["blocked_uploaders"].remove(target_id)
        else:
            return web.json_response(self._settings_payload(guild))

        save_guild_settings(guild.id, settings)
        record_audit(guild.id, session["user_id"], session.get("username"), "block" if blocked else "unblock",
                     details={"user_id": target_id, "username": member.display_name if member else None})
        return web.json_response(self._settings_payload(guild))

    # ---------- Lecture / arrêt ----------

    async def _play_context(self, request: web.Request):
        """Ce que /play et /play-sequence ont en commun : authentification, session, corps JSON, serveur Discord ciblé,
        appartenance de l'appelant à ce serveur, salon vocal connecté. Renvoie (contexte, None) ou (None, réponse d'erreur)."""
        if not self._check_auth(request):
            return None, web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return None, err

        try:
            data = await request.json()
        except Exception:
            return None, web.json_response({"error": "corps JSON invalide"}, status=400)
        if not isinstance(data, dict):
            return None, web.json_response({"error": "corps JSON invalide"}, status=400)

        user_id = session["user_id"]
        guild_id = self.resolve_guild_id(data.get("guild_id"), user_id)
        if guild_id is None:
            return None, web.json_response({
                "error": "Impossible de déterminer le salon Discord cible : rejoignez un salon "
                         "vocal où le bot est présent (/join), puis réessayez."
            }, status=400)

        # L'audio est envoyé dans le vocal de CETTE guilde : l'utilisateur doit en être membre.
        # Sans ça, un guild_id forgé dans la requête (ou le filet "un seul vocal connecté" de
        # resolve_guild_id) permettrait de faire jouer des sons dans le vocal d'une guilde étrangère.
        visible = await self.resolve_visible_guild_ids(int(user_id))
        if str(guild_id) not in visible:
            return None, web.json_response(
                {"error": "Vous n'êtes pas membre du serveur Discord ciblé."},
                status=403,
            )

        vc = self.voice_clients_map.get(guild_id)
        mixer = self.mixers.get(guild_id)
        if vc is None or not vc.is_connected() or mixer is None:
            return None, web.json_response({"error": "Le bot n'est connecté à aucun salon vocal sur ce serveur (utilisez /join)."}, status=409)

        return SimpleNamespace(data=data, user_id=user_id, guild_id=guild_id, visible=visible, mixer=mixer), None

    def _locate_sound(self, sound_id, visible: set):
        """(entrée du catalogue, chemin du fichier, None) ou (None, None, réponse d'erreur) pour un son demandé à la lecture."""
        entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
        if entry is None:
            return None, None, web.json_response({"error": f"son introuvable : {sound_id}"}, status=404)

        # Visibilité du son sur TOUTES les guildes de l'utilisateur (`visible`, calculé plus haut),
        # pas seulement celle où il se trouve en vocal : un membre de plusieurs guildes peut jouer
        # n'importe lequel de ses sons dans le salon où il est, sans partage explicite préalable.
        if not self.sound_visible_to(entry, visible):
            return None, None, web.json_response(
                {"error": "Ce son n'appartient à aucune de vos guildes Discord connues."},
                status=403,
            )

        file_path = SOUNDS_DIR / f"{sound_id}{entry['extension']}"
        if not file_path.exists():
            return None, None, web.json_response({"error": "fichier manquant sur le serveur"}, status=404)
        return entry, file_path, None

    @staticmethod
    def _parse_volume(raw) -> float:
        try:
            volume = float(1.0 if raw is None else raw)
        except (TypeError, ValueError):
            raise ValueError("« volume » doit être un nombre.")
        if not math.isfinite(volume):
            raise ValueError("« volume » doit être un nombre.")
        return min(max(volume, 0.0), 2.0)

    async def _start_playback(self, guild_id: int, user_id, entry: dict, file_path: Path, volume: float,
                              mixer: "MixingAudioSource", *, loop: bool = False, effects_: Optional[dict] = None) -> str:
        """Lance un son dans le salon vocal de cette guilde. Tout ce qui précède — qui a le droit, quel serveur, quel son —
        est déjà vérifié par l'appelant (route HTTP /play ou commande Discord /play) ; ici : plafonds de ressources,
        anti-spam, préparation de la source, ajout au mixeur, activité partagée et statistiques. Lève PlayRefused ;
        renvoie le mode de lecture (« flux », « mémoire »…), pour le journal."""
        sound_id = entry["id"]

        # Plafonds de ressources de l'hébergeur : vérifiés AVANT l'anti-spam (un refus ne doit pas compter dans le quota).
        capacity_error = self._play_capacity_error(sound_id, guild_id, mixer)
        if capacity_error is not None:
            raise PlayRefused(capacity_error, 429, retry_after=2, retry_header=True)

        # Anti-spam réglé par l'admin de la guilde ciblée (où le son sera réellement joué) ;
        # les admins en sont exemptés. Vérifié en dernier : une requête refusée plus haut ne compte pas.
        retry_after = self._check_play_rate(guild_id, str(user_id))
        if retry_after is not None:
            raise PlayRefused(f"Trop de sons envoyés : réessayez dans {retry_after} s.", 429, retry_after=retry_after)

        # On AJOUTE au mixeur plutôt que de remplacer la lecture en cours : plusieurs sons
        # (déclenchés par le même utilisateur ou des utilisateurs différents) se superposent
        # au lieu de s'annuler mutuellement.
        try:
            source, kind = await self._build_play_source(sound_id, entry, file_path, volume, loop=loop, effects_=effects_)
        except ValueError as ex:
            raise PlayRefused(str(ex), 400)

        # Le son part D'ABORD ; la résolution du membre Discord (highlight/avatar) — un appel
        # réseau si non déjà en cache — se fait ENSUITE en tâche de fond, pour ne jamais
        # retarder la lecture elle-même.
        str_user_id = str(user_id) if user_id else None
        on_finish = (lambda: self.clear_activity(sound_id, str_user_id)) if str_user_id else None
        mixer.add(sound_id, source, on_finish=on_finish)

        if str_user_id:
            # Une boucle dure jusqu'à son arrêt : son highlight aussi (le filet de 30 s habituel l'éteindrait trop tôt).
            ttl = LOOP_MAX_MINUTES * 60 + 5 if loop else None
            asyncio.create_task(self._record_play_activity(sound_id, str_user_id, guild_id, ttl))
        return kind

    async def _handle_play(self, request: web.Request) -> web.Response:
        started = time.perf_counter()
        ctx, err = await self._play_context(request)
        if err is not None:
            return err
        data, user_id, guild_id, mixer = ctx.data, ctx.user_id, ctx.guild_id, ctx.mixer

        sound_id = data.get("id")
        entry, file_path, err = self._locate_sound(sound_id, ctx.visible)
        if err is not None:
            return err

        # Options facultatives (voir SERVER_FEATURES) : volume, boucle, effets.
        try:
            volume = self._parse_volume(data.get("volume"))
            effects_ = effects.parse_effects(data.get("effects"))
        except ValueError as ex:
            return web.json_response({"error": str(ex)}, status=400)
        loop_requested = bool(data.get("loop"))
        if loop_requested and LOOP_MAX_MINUTES <= 0:
            return web.json_response({"error": "Les boucles sont désactivées sur ce serveur."}, status=400)

        try:
            kind = await self._start_playback(guild_id, user_id, entry, file_path, volume, mixer,
                                              loop=loop_requested, effects_=effects_)
        except PlayRefused as refused:
            return refused.response()
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        log.info("Lecture « %s » (%s) : %d ms de traitement.", entry.get("name", sound_id), kind, elapsed_ms)

        # « ms » : temps passé côté serveur entre la réception de la requête et l'ajout au mixeur (le client le
        # compare à sa propre mesure pour séparer réseau et traitement, voir la page Diagnostic).
        return web.json_response({"status": "ok", "ms": elapsed_ms})

    async def _handle_play_sequence(self, request: web.Request) -> web.Response:
        """POST /play-sequence : une suite de sons lancée d'un seul appel (« enchaînement »). Corps :
        {"steps": [{"id", "delay_ms"?, "after_end"?, "volume"?, "effects"?}, ...]}. delay_ms : attente avant ce son, comptée
        depuis le DÉBUT du son précédent (ou depuis sa FIN si after_end est vrai) ; pour le premier, depuis maintenant.
        Chaque étape est soumise aux mêmes contrôles (visibilité, quotas) qu'un /play isolé, le tout validé AVANT que
        quoi que ce soit ne parte : une séquence est acceptée en entier ou refusée en entier."""
        started = time.perf_counter()
        ctx, err = await self._play_context(request)
        if err is not None:
            return err
        data, user_id, guild_id = ctx.data, ctx.user_id, ctx.guild_id

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= SEQUENCE_MAX_STEPS:
            return web.json_response({"error": f"« steps » doit contenir de 1 à {SEQUENCE_MAX_STEPS} sons."}, status=400)

        steps = []
        offset_ms = 0
        previous_duration_ms = 0
        for index, raw in enumerate(raw_steps, start=1):
            if not isinstance(raw, dict):
                return web.json_response({"error": f"Étape {index} : un objet est attendu."}, status=400)
            entry, file_path, err = self._locate_sound(raw.get("id"), ctx.visible)
            if err is not None:
                return err
            try:
                delay_ms = raw.get("delay_ms", 0)
                if isinstance(delay_ms, bool) or not isinstance(delay_ms, (int, float)) or not 0 <= delay_ms <= SEQUENCE_MAX_DELAY_MS:
                    raise ValueError(f"« delay_ms » doit être un nombre de 0 à {SEQUENCE_MAX_DELAY_MS}.")
                volume = self._parse_volume(raw.get("volume"))
                effects_ = effects.parse_effects(raw.get("effects"))
            except ValueError as ex:
                return web.json_response({"error": f"Étape {index} : {ex}"}, status=400)

            if index > 1:
                offset_ms += previous_duration_ms if raw.get("after_end") else 0
            offset_ms += int(delay_ms)
            if offset_ms > SEQUENCE_MAX_SPAN_MS:
                return web.json_response({"error": f"Séquence trop longue (plus de {SEQUENCE_MAX_SPAN_MS // 1000} s avant la dernière étape)."}, status=400)

            next_raw = raw_steps[index] if index < len(raw_steps) else None
            needs_end = isinstance(next_raw, dict) and bool(next_raw.get("after_end"))
            previous_duration_ms = await self._played_duration_ms(entry, file_path, effects_) if needs_end else 0
            steps.append((offset_ms, entry, file_path, volume, effects_))

        retry_after = self._check_play_rate(guild_id, str(user_id), count=len(steps))
        if retry_after is not None:
            return web.json_response({
                "error": f"Trop de sons envoyés : réessayez dans {retry_after} s.",
                "retry_after": retry_after,
            }, status=429)

        loop = asyncio.get_running_loop()
        pending = self._pending_steps.setdefault(guild_id, {})
        for step_offset, entry, file_path, volume, effects_ in steps:
            if step_offset <= 0:
                await self._play_step(guild_id, str(user_id), entry, file_path, volume, effects_)
                continue
            handle_box: list = []

            def fire(step=(entry, file_path, volume, effects_), box=handle_box):
                pending.pop(box[0], None)
                asyncio.ensure_future(self._play_step(guild_id, str(user_id), *step))

            handle = loop.call_later(step_offset / 1000, fire)
            handle_box.append(handle)
            pending[handle] = entry["id"]

        return web.json_response({"status": "ok", "steps": len(steps), "ms": round((time.perf_counter() - started) * 1000)})

    async def _played_duration_ms(self, entry: dict, file_path: Path, effects_: Optional[dict]) -> int:
        """Durée jouée d'un son (portion gardée, vitesse comprise), pour enchaîner « après la fin » : le catalogue ne
        garde pas la durée, on la lit avec ffprobe quand une étape suivante en a besoin."""
        start, end = entry.get("trim_start_ms"), entry.get("trim_end_ms")
        if isinstance(start, int) and isinstance(end, int) and end > start:
            duration_ms = end - start
        else:
            seconds = await probe_duration_seconds(file_path)
            duration_ms = round((seconds or 0) * 1000)
        speed = (effects_ or {}).get("speed") or 1.0
        return round(duration_ms / speed)

    async def _play_step(self, guild_id: int, user_id: str, entry: dict, file_path: Path, volume: float, effects_: Optional[dict]) -> None:
        """Lance une étape d'enchaînement (tout a déjà été validé) : le salon peut avoir changé depuis, donc on revérifie."""
        try:
            mixer, vc = self.mixers.get(guild_id), self.voice_clients_map.get(guild_id)
            if mixer is None or vc is None or not vc.is_connected():
                return
            sound_id = entry["id"]
            if self._play_capacity_error(sound_id, guild_id, mixer) is not None:
                return
            source, kind = await self._build_play_source(sound_id, entry, file_path, volume, effects_=effects_)
            mixer.add(sound_id, source, on_finish=lambda: self.clear_activity(sound_id, user_id))
            log.info("Enchaînement : « %s » (%s).", entry.get("name", sound_id), kind)
            asyncio.create_task(self._record_play_activity(sound_id, user_id, guild_id))
        except Exception:
            log.exception("Échec d'une étape d'enchaînement")

    def _cancel_pending_steps(self, guild_id: int, sound_id: Optional[str] = None) -> int:
        """Annule les étapes d'enchaînement pas encore parties (toutes, ou celles d'un son) ; renvoie leur nombre."""
        pending = self._pending_steps.get(guild_id, {})
        cancelled = 0
        for handle, step_sound in list(pending.items()):
            if sound_id is None or step_sound == sound_id:
                handle.cancel()
                del pending[handle]
                cancelled += 1
        return cancelled

    def _check_play_rate(self, guild_id: int, user_id: str, count: int = 1) -> Optional[int]:
        """Anti-spam à la lecture : None si ce son (ou ces `count` sons d'un enchaînement) peut partir (et le
        comptabilise), sinon le nombre de secondes à attendre. Fenêtre glissante de 60 s par (guilde, utilisateur) ;
        limite à 0 = désactivé ; admins de la guilde exemptés."""
        settings = get_guild_settings(guild_id)
        limit = settings["play_rate_per_min"]
        if limit <= 0:
            return None

        guild = self.get_guild(guild_id)
        member = guild.get_member(int(user_id)) if guild is not None and user_id.isdigit() else None
        if member is not None and is_guild_admin(guild, member, settings):
            return None

        now = time.monotonic()
        history = self._play_history.setdefault((guild_id, user_id), deque())
        while history and now - history[0] >= 60:
            history.popleft()
        if len(history) + count > limit:
            return max(1, int(60 - (now - history[0]) + 0.999)) if history else 60
        history.extend([now] * count)
        return None

    async def _record_play_activity(self, sound_id: str, user_id: str, guild_id: int, ttl: Optional[float] = None) -> None:
        """Résout le membre et enregistre l'activité partagée (highlight/avatar) en arrière-plan,
        après que le son a déjà été lancé — voir _handle_play. `ttl` : durée du highlight (boucle)."""
        try:
            member = await self.find_member_async(int(user_id)) if user_id.isdigit() else None
            username = member.display_name if member else None
            avatar_url = str(member.display_avatar.url) if member else None
            self.record_activity(sound_id, user_id, username, avatar_url, guild_id, ttl)
            guild = self.get_guild(guild_id)
            record_play_stat(sound_id, user_id, username, avatar_url, guild_id, guild.name if guild else None)
        except Exception:
            log.exception("Échec de l'enregistrement d'activité en arrière-plan")

    async def _handle_stop_all(self, request: web.Request) -> web.Response:
        """POST /stop : coupe tous les sons du salon ; avec {"sound_id": ...} dans le corps, coupe seulement ce son
        (typiquement une boucle) — les autres continuent. Annule aussi les étapes d'enchaînement pas encore parties."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        guild_id = self.resolve_guild_id(data.get("guild_id"), session["user_id"])
        if guild_id is None:
            return web.json_response({"error": "Impossible de déterminer le salon Discord cible."}, status=400)

        # Même règle que /play : seul un membre de la guilde ciblée peut couper son audio.
        visible = await self.resolve_visible_guild_ids(int(session["user_id"]))
        if str(guild_id) not in visible:
            return web.json_response({"error": "Vous n'êtes pas membre du serveur Discord ciblé."}, status=403)

        mixer = self.mixers.get(guild_id)
        if mixer is None:
            return web.json_response({"error": "Le bot n'est connecté à aucun salon vocal sur ce serveur."}, status=409)

        sound_id = data.get("sound_id")
        if isinstance(sound_id, str) and sound_id:
            return web.json_response({"status": "ok", "stopped": self._stop_guild_sounds(guild_id, sound_id)})

        self._stop_guild_sounds(guild_id)
        return web.json_response({"status": "ok"})

    def _stop_guild_sounds(self, guild_id: int, sound_id: Optional[str] = None) -> Optional[bool]:
        """Coupe tous les sons du salon de cette guilde (et les étapes d'enchaînement pas encore parties), ou un seul son.
        Renvoie True/False selon qu'un son précis jouait (True pour « tout »), ou None si le bot n'est connecté nulle part."""
        mixer = self.mixers.get(guild_id)
        if mixer is None:
            return None
        if sound_id:
            self._cancel_pending_steps(guild_id, sound_id)
            return mixer.stop(sound_id)
        self._cancel_pending_steps(guild_id)
        mixer.clear()  # déclenche aussi les callbacks on_finish : l'activité est effacée immédiatement
        return True

    async def _handle_join_my_channel(self, request: web.Request) -> web.Response:
        """Fait rejoindre le bot au salon vocal où l'utilisateur se trouve actuellement,
        sans passer par la commande /join dans Discord."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        found = await self.find_current_voice_channel(int(session["user_id"]))
        if found is None:
            return web.json_response({
                "error": "Vous n'êtes dans aucun salon vocal sur les serveurs Discord où je suis présent."
            }, status=404)

        guild_id, channel = found
        try:
            await self.join_guild_voice(guild_id, channel)
        except CapacityError as ex:
            return web.json_response({"error": str(ex)}, status=503, headers={"Retry-After": "60"})

        return web.json_response({
            "status": "ok",
            "guild_name": channel.guild.name,
            "channel_name": channel.name,
        })


bot = WaseBoardServer()


@bot.tree.command(name="join", description="Fait rejoindre le bot à votre salon vocal actuel")
async def join(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
        await interaction.response.send_message(tr(interaction, "Vous devez d'abord être connecté à un salon vocal."), ephemeral=True)
        return

    channel = member.voice.channel
    try:
        await bot.join_guild_voice(interaction.guild_id, channel)
    except CapacityError as ex:
        await interaction.response.send_message(tr(interaction, f"⚠️ {ex}"), ephemeral=True)
        return

    await interaction.response.send_message(
        tr(interaction, f"Connecté à **{channel.name}**. Prêt à jouer les sons envoyés par WaseBoard."),
        ephemeral=True
    )


@bot.tree.command(name="leave", description="Déconnecte le bot du salon vocal")
async def leave(interaction: discord.Interaction) -> None:
    left = await bot.leave_guild_voice(interaction.guild_id)
    await interaction.response.send_message(
        tr(interaction, "Déconnecté du salon vocal." if left else "Je ne suis connecté à aucun salon vocal ici."),
        ephemeral=True
    )


@bot.tree.command(name="configurer-invitation",
                   description="Poste un panneau expliquant comment démarrer avec WaseBoard (téléchargement, connexion)")
@app_commands.checks.has_permissions(administrator=True)
async def configurer_invitation(interaction: discord.Interaction) -> None:
    if not PUBLIC_URL or not SHARED_SECRET:
        await interaction.response.send_message(
            "⚠️ `public_url` ou `shared_secret` manquant dans config.json — le panneau serait "
            "posté mais son bouton de connexion directe ne produirait aucun lien utilisable. "
            "Configurez-les puis réessayez.",
            ephemeral=True,
        )
        return
    await interaction.channel.send(
        "**Comment démarrer avec WaseBoard**\n"
        "1. Téléchargez l'application (bouton ci-dessous).\n"
        "2. Connectez-vous avec votre compte Discord quand elle le demande — ça suffit, elle se "
        "connecte automatiquement à l'instance publique par défaut.\n\n"
        "Ce serveur utilise sa **propre instance** WaseBoard (pas celle par défaut) ? Cliquez "
        "« Connexion directe à ce serveur » pour vous y connecter directement.",
        view=InviteView(),
    )
    await interaction.response.send_message("Panneau posté.", ephemeral=True)


@bot.tree.command(name="inviter-bot",
                   description="Poste un bouton pour ajouter ce bot WaseBoard à un autre serveur Discord")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def inviter_bot(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        # Défense en profondeur (comme /panneau et /diagnostic) : default_permissions cache déjà la
        # commande aux non-admins, mais un admin de serveur peut rouvrir l'accès via les réglages
        # d'intégration Discord — ce contrôle explicite ne dépend donc pas uniquement de Discord.
        await interaction.response.send_message("Cette commande est réservée aux administrateurs du serveur.", ephemeral=True)
        return
    invite_url = diagnostics.build_invite_url(bot.oauth_client_id())
    invite_view = discord.ui.View()
    invite_view.add_item(discord.ui.Button(label="➕ Ajouter WaseBoard à mon serveur", style=discord.ButtonStyle.link, url=invite_url))
    await interaction.channel.send(
        "Ajoutez ce bot WaseBoard à un **autre** serveur Discord (le vôtre ou celui d'un ami) — chaque "
        "serveur obtient sa **propre bibliothèque de sons**, totalement indépendante des autres : pratique "
        "si vous gérez plusieurs communautés Discord. Il faut avoir le droit « Gérer le serveur » sur ce "
        "nouveau serveur pour l'ajouter.",
        view=invite_view,
    )
    await interaction.response.send_message("Bouton posté.", ephemeral=True)


@bot.tree.command(name="panneau",
                   description="Poste un panneau de boutons (rejoindre le vocal, aide) utilisable par tous les membres")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def panneau(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        # Défense en profondeur (comme /diagnostic) : default_permissions ci-dessus cache déjà la commande aux
        # non-admins dans l'interface, mais un admin de serveur peut rouvrir l'accès via les réglages d'intégration
        # Discord — ce contrôle explicite ne dépend donc pas uniquement de ce que Discord affiche.
        await interaction.response.send_message("Cette commande est réservée aux administrateurs du serveur.", ephemeral=True)
        return
    await interaction.channel.send(
        "**WaseBoard** — une fois connecté dans un salon vocal, cliquez 🔊 pour que le bot vous rejoigne, "
        "puis jouez vos sons depuis l'application.",
        view=MemberPanelView(),
    )
    await interaction.response.send_message("Panneau posté.", ephemeral=True)


@bot.tree.command(name="bot-setup",
                   description="Poste un panneau tout-en-un (démarrer, actions, inviter ailleurs, liens) — une seule commande")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def bot_setup(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        await interaction.response.send_message("Cette commande est réservée aux administrateurs du serveur.", ephemeral=True)
        return
    # Embed plutôt que du texte brut : la barre de couleur + les champs distincts rendent le
    # panneau plus engageant qu'un bloc de markdown, et l'ordre place "autre serveur" en dernier
    # (une précision pour le cas minoritaire, pas la première chose lue).
    embed = discord.Embed(
        title="🎛️ WaseBoard",
        description="Soundboard partagé pour Discord : cliquez un son, il joue à la fois sur vos "
                     "enceintes et dans le salon vocal, pour tout le monde.",
        color=discord.Color(0x38BDF8),
    )
    if bot.user is not None:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.add_field(
        name="🚀 Démarrer",
        value="Téléchargez l'application et connectez-vous avec Discord — ça suffit, elle se connecte "
              "automatiquement à l'instance publique par défaut.",
        inline=False,
    )
    embed.add_field(
        name="🔊 Une fois connecté",
        value="Rejoignez un salon vocal puis cliquez 🔊, ou cliquez directement un son dans l'application.",
        inline=False,
    )
    embed.add_field(
        name="✨ Pour aller plus loin",
        value="Ajoutez ce bot à un autre serveur Discord (bibliothèque de sons indépendante), ou "
              "retrouvez le site, la communauté et le code source ci-dessous.",
        inline=False,
    )
    embed.add_field(
        name="🏠 Un autre serveur WaseBoard (auto-hébergé) ?",
        value="Utilisez « Connexion directe à ce serveur » pour vous y connecter directement.",
        inline=False,
    )
    embed.set_footer(text="WaseBoard — projet open-source")
    await interaction.channel.send(embed=embed, view=BotSetupView())
    await interaction.response.send_message("Panneau posté.", ephemeral=True)


@bot.tree.command(name="diagnostic",
                   description="Vérifie la configuration de WaseBoard sur ce serveur (réservé aux administrateurs)")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def diagnostic(interaction: discord.Interaction) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        await interaction.response.send_message("Cette commande est réservée aux administrateurs du serveur.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        checks, _ = await diagnostics.run_checks(CONFIG, DATA_DIR, bot.http_session)
        checks += diagnostics.check_guild(interaction.guild, member)
        if MAX_GUILDS > 0 and len(bot.guilds) >= MAX_GUILDS:
            checks.append(diagnostics.Check("warn", "Capacité de l'instance",
                                            f"{len(bot.guilds)} serveurs sur {MAX_GUILDS} autorisés : les nouveaux serveurs sont refusés."))
        else:
            checks.append(diagnostics.Check("info", "Serveurs connectés", f"{len(bot.guilds)} serveur(s) Discord sur cette instance"
                                            + (f" (maximum {MAX_GUILDS})" if MAX_GUILDS > 0 else "")))
        text = diagnostics.render(checks)
    except Exception:
        log.exception("Échec de /diagnostic")
        text = "❌ Le diagnostic a échoué (voir les journaux du serveur)."
    await interaction.followup.send(text, ephemeral=True)


@bot.tree.command(name="instance",
                   description="Santé et capacité de cette instance WaseBoard (propriétaire de l'application Discord)")
@app_commands.default_permissions(administrator=True)
async def instance_command(interaction: discord.Interaction) -> None:
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message(
            "Cette commande est réservée à la personne qui héberge ce serveur WaseBoard "
            "(propriétaire de l'application Discord).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        text = render_instance_stats(await bot.instance_stats())
    except Exception:
        log.exception("Échec de /instance")
        text = "❌ Impossible de calculer les statistiques (voir les journaux du serveur)."
    await interaction.followup.send(text[:1990], ephemeral=True)


# ---------- Commandes Discord : jouer, arrêter, classements ----------

def _choice_label(entry: dict) -> str:
    return f"{entry['emoji'] + ' ' if entry.get('emoji') else ''}{entry['name']}"[:100]


async def _catalog_for_user(user_id: int) -> list[dict]:
    """Sons que cette personne a le droit de jouer (ceux de ses serveurs + les partagés) : même règle que l'API HTTP."""
    visible = await bot.resolve_visible_guild_ids(user_id)
    return bot.catalog_visible_to(load_catalog(), visible)


def find_sound_for_command(raw: str, catalog: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """(son, None) ou (None, message) : `raw` est l'identifiant choisi dans la liste proposée, ou un nom tapé à la main
    (accepté s'il est sans ambiguïté, un simple morceau de nom à partir de 3 lettres). `catalog` ne contient déjà que les sons visibles par la personne."""
    raw = (raw or "").strip()
    by_id = next((e for e in catalog if e["id"] == raw), None)
    if by_id is not None:
        return by_id, None
    needle = raw.casefold()
    exact = [e for e in catalog if e["name"].casefold() == needle]
    if len(exact) == 1:
        return exact[0], None
    # Un morceau de nom n'est accepté qu'à partir de 3 lettres : « d » ne doit pas jouer le premier son qui contient un d.
    partial = [e for e in catalog if needle in e["name"].casefold()] if len(needle) >= 3 else []
    if len(exact) > 1 or len(partial) > 1:
        return None, f"Plusieurs sons correspondent à « {raw[:60]} » : choisissez-en un dans la liste proposée."
    if len(partial) == 1:
        return partial[0], None
    return None, f"Aucun son ne correspond à « {raw[:60]} »."


async def sound_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Liste de sons proposée pendant la frappe (25 au plus, limite de Discord) : ceux qui commencent par ce qui est
    tapé d'abord, puis ceux du serveur courant, puis par ordre alphabétique."""
    try:
        catalog = await _catalog_for_user(interaction.user.id)
    except Exception:
        log.warning("Autocomplétion impossible", exc_info=True)
        return []
    needle = (current or "").strip().casefold()
    here = str(interaction.guild_id) if interaction.guild_id else None
    matches = [e for e in catalog if needle in e["name"].casefold()]
    matches.sort(key=lambda e: (not e["name"].casefold().startswith(needle), e.get("guild_id") != here, e["name"].casefold()))
    return [app_commands.Choice(name=_choice_label(e), value=e["id"]) for e in matches[:25]]


def _embed_from(view: dict) -> discord.Embed:
    embed = discord.Embed(title=view["title"][:256], description=view["description"], color=discord.Color(0x38BDF8))
    for name, value in view["fields"]:
        embed.add_field(name=name, value=value[:1024], inline=False)
    return embed


@bot.tree.command(name="play", description="Joue un son dans votre salon vocal, sans ouvrir l'application")
@app_commands.guild_only()
@app_commands.describe(son="Le son à jouer (tapez quelques lettres pour chercher)")
@app_commands.autocomplete(son=sound_autocomplete)
async def play_command(interaction: discord.Interaction, son: str) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
        await interaction.response.send_message(tr(interaction, "Vous devez d'abord être connecté à un salon vocal."), ephemeral=True)
        return
    channel, guild_id = member.voice.channel, interaction.guild_id
    await interaction.response.defer(ephemeral=True, thinking=True)  # rejoindre le salon peut prendre un instant

    entry, problem = find_sound_for_command(son, await _catalog_for_user(member.id))
    if entry is None:
        await interaction.followup.send(tr(interaction, f"❌ {problem}"), ephemeral=True)
        return
    file_path = SOUNDS_DIR / f"{entry['id']}{entry['extension']}"
    if not file_path.exists():
        await interaction.followup.send(tr(interaction, "❌ Le fichier de ce son est introuvable sur le serveur."), ephemeral=True)
        return

    vc = bot.voice_clients_map.get(guild_id)
    if vc is not None and vc.is_connected():
        if vc.channel.id != channel.id:
            await interaction.followup.send(
                tr(interaction, f"Le bot est dans « {vc.channel.name} ». Rejoignez ce salon, ou utilisez /join pour l'amener dans le vôtre."),
                ephemeral=True)
            return
    else:
        try:
            await bot.join_guild_voice(guild_id, channel)
        except CapacityError as ex:
            await interaction.followup.send(tr(interaction, f"⚠️ {ex}"), ephemeral=True)
            return
        except Exception:
            log.exception("/play : impossible de rejoindre le salon vocal")
            await interaction.followup.send(tr(interaction, "❌ Impossible de rejoindre votre salon vocal (le bot a-t-il la permission « Se connecter » ?)."), ephemeral=True)
            return

    try:
        await bot._start_playback(guild_id, member.id, entry, file_path, 1.0, bot.mixers[guild_id])
    except PlayRefused as refused:
        await interaction.followup.send(tr(interaction, f"⚠️ {refused.message}"), ephemeral=True)
        return
    await interaction.followup.send(tr(interaction, f"▶️ {_choice_label(entry)}"), ephemeral=True)


@bot.tree.command(name="stop", description="Coupe les sons en cours dans le salon vocal")
@app_commands.guild_only()
@app_commands.describe(son="Couper seulement ce son (une boucle, par exemple) ; sans lui, tous les sons sont coupés")
@app_commands.autocomplete(son=sound_autocomplete)
async def stop_command(interaction: discord.Interaction, son: Optional[str] = None) -> None:
    guild_id = interaction.guild_id
    if bot.mixers.get(guild_id) is None:
        await interaction.response.send_message(tr(interaction, "Je ne suis connecté à aucun salon vocal ici."), ephemeral=True)
        return
    if not son:
        bot._stop_guild_sounds(guild_id)
        await interaction.response.send_message(tr(interaction, "⏹️ Sons coupés."), ephemeral=True)
        return

    entry, problem = find_sound_for_command(son, await _catalog_for_user(interaction.user.id))
    if entry is None:
        await interaction.response.send_message(tr(interaction, f"❌ {problem}"), ephemeral=True)
        return
    stopped = bot._stop_guild_sounds(guild_id, entry["id"])
    await interaction.response.send_message(
        tr(interaction, f"⏹️ {_choice_label(entry)} coupé." if stopped else f"{_choice_label(entry)} ne jouait pas."), ephemeral=True)


@bot.tree.command(name="top", description="Les sons les plus joués et les membres les plus actifs du serveur")
@app_commands.guild_only()
@app_commands.describe(periode="Période étudiée (30 jours par défaut)",
                       partager="Poster le classement dans le salon (sinon, vous seul le voyez)")
@app_commands.choices(periode=[app_commands.Choice(name="7 derniers jours", value=7),
                               app_commands.Choice(name="30 derniers jours", value=30),
                               app_commands.Choice(name="90 derniers jours", value=90),
                               app_commands.Choice(name="12 derniers mois", value=365)])
async def top_command(interaction: discord.Interaction, periode: int = 30, partager: bool = False) -> None:
    await interaction.response.defer(ephemeral=not partager, thinking=True)
    try:
        names = {e["id"]: e for e in load_catalog()}
        stats = await asyncio.to_thread(aggregate_play_stats, str(interaction.guild_id), periode, names)
        language = messages.discord_language(getattr(interaction, "locale", None))
        view = reports.render_top(stats, interaction.guild.name if interaction.guild else reports.texts(language)["this_server"], periode, language)
    except Exception:
        log.exception("Échec de /top")
        await interaction.followup.send(tr(interaction, "❌ Impossible de calculer le classement (voir les journaux du serveur)."), ephemeral=True)
        return
    await interaction.followup.send(embed=_embed_from(view))


@bot.tree.command(name="bilan", description="Le bilan de l'année : sons les plus joués, membres les plus actifs, vos chiffres")
@app_commands.guild_only()
@app_commands.describe(annee="Année du bilan (l'année en cours par défaut)",
                       partager="Poster le bilan dans le salon (sinon, vous seul le voyez)")
async def bilan_command(interaction: discord.Interaction, annee: Optional[app_commands.Range[int, 2020, 2100]] = None,
                        partager: bool = False) -> None:
    year = annee or datetime.now().year
    await interaction.response.defer(ephemeral=not partager, thinking=True)
    try:
        names = {e["id"]: e for e in load_catalog()}
        guild_key, user_key = str(interaction.guild_id), str(interaction.user.id)
        report = await asyncio.to_thread(lambda: reports.year_report(_read_jsonl(STATS_PATH), guild_key, year, user_key, names))
        language = messages.discord_language(getattr(interaction, "locale", None))
        view = reports.render_bilan(report, interaction.guild.name if interaction.guild else reports.texts(language)["this_server"], language)
    except Exception:
        log.exception("Échec de /bilan")
        await interaction.followup.send(tr(interaction, "❌ Impossible de calculer le bilan (voir les journaux du serveur)."), ephemeral=True)
        return
    await interaction.followup.send(embed=_embed_from(view))


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
    """Quitte automatiquement un salon vocal dès qu'il ne reste plus que des bots (ou personne) dedans."""
    try:
        channel = before.channel
        if channel is None:
            return  # ce changement ne concerne pas un départ de salon

        guild_id = channel.guild.id
        vc = bot.voice_clients_map.get(guild_id)
        bot_is_here = vc is not None and vc.is_connected() and vc.channel.id == channel.id

        if not bot_is_here:
            return  # ce salon n'est pas celui où le bot est connecté

        remaining_humans = [m for m in channel.members if not m.bot]
        if not remaining_humans:
            await bot.leave_guild_voice(guild_id)
            log.info("Salon vocal « %s » vide sur %s : déconnexion automatique.", channel.name, channel.guild.name)
    except Exception:
        log.exception("Erreur dans on_voice_state_update")


def main() -> int:
    # Modes « ligne de commande » : n'ouvrent AUCUNE connexion à la passerelle Discord (REST seulement), donc sans
    # risque de doublon avec une instance déjà lancée avec le même jeton.
    if "--check" in sys.argv or "--invite-url" in sys.argv:
        return diagnostics.cli(sys.argv[1:], CONFIG, DATA_DIR)

    if not BOT_TOKEN:
        print("Jeton du bot manquant : renseignez bot_token dans config.json (copiez config.example.json) ou la "
              "variable d'environnement WASEBOARD_BOT_TOKEN — voir README.md.", file=sys.stderr)
        return 1
    # `docker stop` / `systemctl stop` envoient SIGTERM : sans gestionnaire, un processus PID 1 (conteneur) l'ignore et
    # Docker finit par le tuer au bout de 10 s ; en le traitant comme Ctrl+C, discord.py ferme proprement ses connexions
    # (salons vocaux quittés, sessions HTTP fermées) puis le processus se termine aussitôt, avec le code 0.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        # log_handler=None : le logging est déjà configuré en haut du fichier ; sans ça, discord.py ajoute son propre
        # gestionnaire et chaque ligne apparaît en double dans le journal (systemd comme `docker compose logs`).
        bot.run(BOT_TOKEN, log_handler=None)
    except discord.LoginFailure:
        print("❌ Discord a refusé le jeton du bot (invalide ou réinitialisé). Portail Discord > votre application > "
              "Bot > Reset Token, puis mettez-le à jour. `python server.py --check` détaille le problème.", file=sys.stderr)
        return 1
    except discord.PrivilegedIntentsRequired:
        print("❌ L'intent « Server Members » n'est pas activé pour ce bot. Portail Discord > votre application > Bot > "
              "Privileged Gateway Intents > activez « Server Members Intent », puis relancez.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
