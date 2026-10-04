"""
Serveur WaseBoard : catalogue de sons partagé (API HTTP) + bot Discord qui les joue dans le
salon vocal. Peut être connecté à plusieurs serveurs Discord (guilds) en même temps, chacun
avec sa propre connexion vocale et son propre mixeur audio.

Voir README.md pour la mise en place complète.
"""

import asyncio
import hashlib
import html
import ipaddress
import json
import logging
import os
import secrets
import shutil
import signal
import sys
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import aiohttp
import discord
import numpy as np
from discord import app_commands
from discord.ext import commands
from aiohttp import web

import diagnostics
from wb_config import DATA_DIR, ConfigError, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("waseboard-server")

# Configuration : config.json + variables d'environnement WASEBOARD_* (voir wb_config.py) ; toutes les données vivent
# dans DATA_DIR (à côté de ce script par défaut, ou WASEBOARD_DATA_DIR — le volume /data en Docker).
SOUNDS_DIR = DATA_DIR / "sounds_data"
CATALOG_PATH = SOUNDS_DIR / "catalog.json"
SHARED_CATEGORIES_PATH = DATA_DIR / "shared_categories.json"
OAUTH_SESSIONS_PATH = DATA_DIR / "oauth_sessions.json"
STATS_PATH = DATA_DIR / "stats.jsonl"
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
# Réglages imposés par l'hébergeur aux guildes : valeurs PAR DÉFAUT de celles qui n'ont encore rien enregistré
# (leurs admins peuvent les modifier) et PLAFONDS (que leurs admins ne peuvent pas dépasser ; « illimité » n'est alors
# plus permis pour ce réglage). Clés possibles : voir wb_config.GUILD_LIMIT_KEYS.
DEFAULT_GUILD_LIMITS: dict = CONFIG.get("default_guild_limits") or {}
GUILD_LIMIT_CEILINGS: dict = CONFIG.get("guild_limit_ceilings") or {}
STARTED_AT = time.time()

SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
TRASH_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma"}

# Format attendu par discord.py pour une frame audio : 20ms de PCM stéréo 16 bits @ 48kHz.
FRAME_BYTES = 3840
SILENCE = b"\x00" * FRAME_BYTES

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


class MixingAudioSource(discord.AudioSource):
    """
    Source audio persistante d'un salon : mixe plusieurs sons différents (ajoutés via add(),
    indexés par sound_id) à chaque frame de 20ms au lieu de se remplacer. Rejouer le MÊME son
    coupe et relance l'instance précédente plutôt que de la superposer. Renvoie du silence
    quand rien ne joue (le voice_client ne doit jamais voir de fin de flux).

    Le callback "on_finish" optionnel se déclenche à la fin réelle du son (ou à son remplacement),
    utilisé pour effacer le highlight partagé au bon moment.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[discord.AudioSource, Optional[callable]]] = {}
        self._lock = threading.Lock()

    def add(self, key: str, source: discord.AudioSource, on_finish: Optional[callable] = None) -> None:
        with self._lock:
            previous = self._entries.get(key)
            self._entries[key] = (source, on_finish)
        # Coupe l'ancienne instance après avoir publié la nouvelle (jamais de trou pour cette
        # clé), hors du verrou (cleanup/on_finish peuvent être lents).
        if previous is not None:
            old_source, old_on_finish = previous
            try:
                old_source.cleanup()
            except Exception:
                pass
            if old_on_finish is not None:
                try:
                    old_on_finish()
                except Exception:
                    log.exception("Erreur dans un callback on_finish (relance)")

    def count(self) -> int:
        """Nombre de sons actuellement en lecture sur ce mixeur (un processus ffmpeg chacun)."""
        with self._lock:
            return len(self._entries)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._entries

    def clear(self) -> None:
        """Coupe immédiatement tous les sons en cours sur ce mixeur (bouton "Stop tout")."""
        with self._lock:
            entries, self._entries = self._entries, {}
        self._finish_entries(entries.values())

    def read(self) -> bytes:
        with self._lock:
            snapshot = list(self._entries.items())

        if not snapshot:
            return SILENCE

        frames, finished = [], []
        for key, (source, _) in snapshot:
            try:
                data = source.read()
            except Exception:
                data = b""
            if data:
                frames.append(data)
            else:
                finished.append(key)

        if finished:
            finished_entries = []
            with self._lock:
                for key in finished:
                    current = self._entries.get(key)
                    # Ne retire QUE si c'est toujours la même instance qu'au moment du snapshot :
                    # entre-temps, un nouvel appui a pu déjà remplacer cette clé par une relance
                    # (voir add()) — dans ce cas, il ne faut surtout pas effacer la nouvelle entrée.
                    if current is not None and current[0] is source:
                        del self._entries[key]
                        finished_entries.append(current)
            self._finish_entries(finished_entries)

        if not frames:
            return SILENCE

        if len(frames) == 1:
            data = frames[0]
            return data if len(data) == FRAME_BYTES else data.ljust(FRAME_BYTES, b"\x00")

        # Saturation appliquée après chaque son ajouté (pas une somme brute clampée une seule
        # fois à la fin) : reproduit exactement le comportement de l'ancienne boucle scalaire,
        # où l'ordre d'empilement des sons peut influencer un résultat déjà saturé.
        mixed = np.zeros(1920, dtype=np.int32)
        for data in frames:
            if len(data) < FRAME_BYTES:
                data = data.ljust(FRAME_BYTES, b"\x00")
            samples = np.frombuffer(data[:FRAME_BYTES], dtype=np.int16).astype(np.int32)
            mixed = np.clip(mixed + samples, -32768, 32767)

        return mixed.astype(np.int16).tobytes()

    @staticmethod
    def _finish_entries(entries) -> None:
        for source, on_finish in entries:
            try:
                source.cleanup()
            except Exception:
                pass
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:
                    log.exception("Erreur dans un callback on_finish")

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        with self._lock:
            entries, self._entries = self._entries, {}
        self._finish_entries(entries.values())


intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True
intents.members = True  # requis pour résoudre un membre par id ; à activer aussi dans le
                         # Portail Développeur > Bot > "Server Members Intent".


# ---------- Limitation de débit des routes publiques ----------

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
    return None


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


def render_connect_page(link: Optional[str], nonce: str = "") -> str:
    """Page d'invitation (français). link=None : code inconnu ou expiré."""
    if link is None:
        body = (
            "<h1>Ce lien n'est plus valide</h1>"
            "<p>Il a expiré (ou n'a jamais existé). Retournez sur Discord et cliquez de nouveau sur "
            "« Recevoir mon lien WaseBoard » pour en obtenir un nouveau.</p>"
        )
        script = ""
    else:
        body = (
            "<h1>Ouvrir WaseBoard</h1>"
            "<p>WaseBoard va s'ouvrir et se connecter tout seul à ce serveur. Si votre navigateur demande "
            "l'autorisation d'ouvrir l'application, acceptez.</p>"
            f'<a class="btn" href="{html.escape(link)}">Ouvrir WaseBoard</a>'
            f'<p>WaseBoard n\'est pas encore installé ? <a class="dl" href="{html.escape(DOWNLOAD_URL)}">Téléchargez-le ici</a>, '
            "puis revenez cliquer sur le bouton.</p>"
            "<p><small>Rien ne se passe ? Copiez ce lien, puis collez-le dans la fenêtre Exécuter de Windows "
            "(touches Windows + R).</small></p>"
            f'<textarea readonly onclick="this.select()">{html.escape(link)}</textarea>'
        )
        script = f'<script nonce="{nonce}">setTimeout(function(){{location.href={json.dumps(link)}}},400)</script>'
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>WaseBoard</title>'
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


async def _handle_join_voice_button(interaction: discord.Interaction) -> None:
    """Logique du bouton "Rejoindre mon vocal", partagée par MemberPanelView et BotSetupView (deux
    boutons différents, même comportement — évite de la dupliquer)."""
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
        await interaction.response.send_message(
            "Connectez-vous d'abord à un salon vocal sur Discord, puis recliquez ce bouton.", ephemeral=True)
        return
    channel = member.voice.channel
    try:
        await bot.join_guild_voice(interaction.guild_id, channel)
    except CapacityError as ex:
        await interaction.response.send_message(f"⚠️ {ex}", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Connecté à **{channel.name}**. Prêt à jouer les sons envoyés par WaseBoard.", ephemeral=True)


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
        await interaction.response.send_message(PANEL_HELP_TEXT, ephemeral=True)


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
                "Lien non configuré côté serveur (public_url/shared_secret manquants dans config.json).",
                ephemeral=True,
            )
            return
        page_url = f"{PUBLIC_URL.rstrip('/')}/connect/{create_invite_code()}"
        link_view = discord.ui.View()
        link_view.add_item(discord.ui.Button(label="🚀 Ouvrir WaseBoard", style=discord.ButtonStyle.link, url=page_url))
        await interaction.response.send_message(
            "Cliquez sur le bouton : WaseBoard s'ouvre et se connecte directement à CE serveur "
            f"(utile si votre application est reliée à une autre instance — le lien expire dans "
            f"{INVITE_CODE_TTL_SECONDS // 60} minutes).",
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
                "Lien non configuré côté serveur (public_url/shared_secret manquants dans config.json).",
                ephemeral=True,
            )
            return
        page_url = f"{PUBLIC_URL.rstrip('/')}/connect/{create_invite_code()}"
        link_view = discord.ui.View()
        link_view.add_item(discord.ui.Button(label="🚀 Ouvrir WaseBoard", style=discord.ButtonStyle.link, url=page_url))
        await interaction.response.send_message(
            "Cliquez sur le bouton : WaseBoard s'ouvre et se connecte directement à CE serveur "
            f"(utile si votre application est reliée à une autre instance — le lien expire dans "
            f"{INVITE_CODE_TTL_SECONDS // 60} minutes).",
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
        await interaction.response.send_message(PANEL_HELP_TEXT, ephemeral=True)

    @discord.ui.button(label="➕ Ajouter à un autre serveur", style=discord.ButtonStyle.secondary,
                        custom_id="waseboard_setup_invite", row=3)
    async def invite_other_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        invite_url = diagnostics.build_invite_url(bot.oauth_client_id())
        invite_view = discord.ui.View()
        invite_view.add_item(discord.ui.Button(label="➕ Ajouter WaseBoard à mon serveur", style=discord.ButtonStyle.link, url=invite_url))
        await interaction.response.send_message(
            "Ajoutez ce bot WaseBoard à un **autre** serveur Discord (le vôtre ou celui d'un ami) — chaque "
            "serveur obtient sa **propre bibliothèque de sons**, totalement indépendante des autres : pratique "
            "si vous gérez plusieurs communautés Discord. Il faut avoir le droit « Gérer le serveur » sur ce "
            "nouveau serveur pour l'ajouter.",
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

        # Sessions OAuth2 Discord : session_token (émis par WaseBoard) -> identité vérifiée +
        # jetons Discord. Chargées depuis disque pour survivre à un redémarrage.
        self.oauth_sessions: dict[str, dict] = load_oauth_sessions()
        self._session_refresh_locks: dict[str, asyncio.Lock] = {}
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._commands_synced = False  # on_ready peut se rejouer à chaque reconnexion
        self._trash_purge_task: Optional[asyncio.Task] = None
        # Anti-spam à la lecture : (guild_id, user_id) -> horodatages (monotonic) des lectures récentes
        self._play_history: dict[tuple[int, str], deque] = {}

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        self._trash_purge_task = asyncio.create_task(self._trash_purge_loop())

        # Enregistrement persistant (custom_id fixe) : le bouton reste fonctionnel sur le
        # message déjà posté par /configurer-invitation après un redémarrage, sans avoir à
        # retenir où il a été posté.
        self.add_view(InviteView())
        self.add_view(MemberPanelView())
        self.add_view(BotSetupView())

        if not GUILD_ID:
            await self.tree.sync()
            log.info("Commandes slash synchronisées globalement (peut prendre jusqu'à 1h à apparaître).")
        # Avec guild_id configuré : synchronisation instantanée PAR serveur, faite dans on_ready /
        # on_guild_join (self.guilds n'est pas encore rempli à ce stade).

        await self._start_http_server()

    def oauth_client_id(self) -> str:
        """Identifiant client OAuth2 : celui de config.json s'il est renseigné, sinon celui de l'application
        Discord du bot (c'est le même nombre) — l'admin n'a plus que le secret client à copier."""
        if OAUTH2_CLIENT_ID:
            return OAUTH2_CLIENT_ID
        application_id = self.application_id or (self.user.id if self.user else None)
        return str(application_id) if application_id else ""

    async def _sync_commands_to_guild(self, guild: discord.abc.Snowflake) -> None:
        """Rend les commandes slash disponibles immédiatement sur ce serveur (la synchro globale
        met jusqu'à 1h à se propager). Sans ça, seul le serveur de guild_id aurait /join & co —
        le bot étant multi-serveurs, tous ceux où il est présent doivent les avoir."""
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self) -> None:
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

    async def close(self) -> None:
        if self._trash_purge_task is not None:
            self._trash_purge_task.cancel()
        if self.http_session is not None:
            await self.http_session.close()
        await super().close()

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
            else:
                guilds_to_fetch.append(guild)

        if guilds_to_fetch:
            async def _safe_fetch(guild: discord.Guild):
                try:
                    return guild.id, await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    return guild.id, None

            # En parallèle (pas guilde par guilde) : un cache froid coûte alors le temps du PLUS
            # LENT appel REST, pas leur somme — c'est ce qui rendait perceptible le tout premier
            # clic (ou le premier après un moment d'inactivité, une fois le cache expiré).
            for gid, member in await asyncio.gather(*(_safe_fetch(g) for g in guilds_to_fetch)):
                if member is not None:
                    member_by_guild_id[gid] = member

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

    def record_activity(self, sound_id: str, user_id: str, username: Optional[str], avatar_url: Optional[str], guild_id: int) -> None:
        with self._activity_lock:
            self.active_plays.setdefault(sound_id, {})[user_id] = {
                "username": username or "?",
                "avatar_url": avatar_url or "",
                "expires_at": time.time() + ACTIVITY_TTL_SECONDS,
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
                              middlewares=[rate_limit_middleware] if RATE_LIMITER.limit > 0 else [])
        app.router.add_get("/sounds", self._handle_list_sounds)
        app.router.add_get("/sounds/{id}/file", self._handle_get_file)
        app.router.add_post("/sounds", self._handle_upload_sound)
        app.router.add_patch("/sounds/{id}", self._handle_update_sound)
        app.router.add_delete("/sounds/{id}", self._handle_delete_sound)
        app.router.add_post("/play", self._handle_play)
        app.router.add_post("/stop", self._handle_stop_all)
        app.router.add_post("/join-my-channel", self._handle_join_my_channel)
        app.router.add_get("/status", self._handle_status)
        app.router.add_get("/health", self._handle_health)  # publique et minimale : sonde Docker/Caddy/supervision
        app.router.add_get("/connect/{code}", self._handle_connect_page)  # publique : voir _handle_connect_page
        app.router.add_get("/activity", self._handle_activity)
        app.router.add_get("/my-guilds", self._handle_my_guilds)
        app.router.add_get("/shared-categories", self._handle_get_shared_categories)
        app.router.add_post("/shared-categories/sounds", self._handle_shared_category_sound)
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

        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, HTTP_HOST, HTTP_PORT)
        await site.start()
        log.info("Serveur HTTP prêt sur %s:%s (catalogue + ordres de lecture WaseBoard).", HTTP_HOST, HTTP_PORT)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health : 200 quand le bot est connecté à Discord, 503 tant qu'il démarre (ou s'il est déconnecté).
        Volontairement sans authentification et sans aucun détail (ni nom de serveur ni compteur) : elle sert à
        un HEALTHCHECK Docker, à Caddy ou à une sonde externe."""
        ready = self.is_ready()
        return web.json_response({"status": "ok" if ready else "starting"}, status=200 if ready else 503,
                                 headers={"Cache-Control": "no-store"})

    def _check_auth(self, request: web.Request) -> bool:
        if not SHARED_SECRET:
            return True
        return request.headers.get("X-WaseBoard-Token") == SHARED_SECRET

    async def _handle_connect_page(self, request: web.Request) -> web.Response:
        """GET /connect/{code} : page ouverte depuis le bouton Discord (donc SANS en-tête d'authentification, par
        conception). Elle ne livre le lien de connexion — adresse + secret partagé — qu'avec un code valide, émis
        à un membre qui a cliqué sur le bouton ; sans code valide, elle ne révèle rien."""
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
        code = request.match_info["code"]
        if not (PUBLIC_URL and SHARED_SECRET and invite_code_is_valid(code)):
            return web.Response(text=render_connect_page(None), content_type="text/html", status=404, headers=headers)

        nonce = secrets.token_urlsafe(12)
        headers["Content-Security-Policy"] = (
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'"
        )
        return web.Response(text=render_connect_page(build_connect_link(), nonce), content_type="text/html", headers=headers)

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

    async def _handle_oauth_exchange(self, request: web.Request) -> web.Response:
        """Échange un code d'autorisation Discord contre une session WaseBoard. Le
        client_secret Discord reste ici, jamais transmis au client."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not self.oauth_client_id() or not OAUTH2_CLIENT_SECRET:
            return web.json_response({"error": "OAuth2 non configuré côté serveur"}, status=500)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        code = data.get("code")
        redirect_uri = data.get("redirect_uri")
        if not code or not redirect_uri:
            return web.json_response({"error": "'code' et 'redirect_uri' requis"}, status=400)
        # Défense en profondeur : n'accepte que des redirections en boucle locale, cohérent
        # avec la restriction que Discord impose déjà côté autorisation — évite d'utiliser cet
        # endpoint comme proxy générique d'échange de code vers un redirect_uri arbitraire.
        if not (redirect_uri.startswith("http://127.0.0.1:") or redirect_uri.startswith("http://localhost:")):
            return web.json_response({"error": "redirect_uri non autorisé"}, status=400)

        try:
            token_data = await self._discord_token_request({
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.oauth_client_id(),
                "client_secret": OAUTH2_CLIENT_SECRET,
            })
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

        return web.json_response({
            "session_token": session_token,
            "user_id": session["user_id"],
            "username": session["username"],
            "avatar_url": session["avatar_url"],
        })

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

    async def _handle_play(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "corps JSON invalide"}, status=400)

        user_id = session["user_id"]
        guild_id = self.resolve_guild_id(data.get("guild_id"), user_id)
        if guild_id is None:
            return web.json_response({
                "error": "Impossible de déterminer le salon Discord cible : rejoignez un salon "
                         "vocal où le bot est présent (/join), puis réessayez."
            }, status=400)

        # L'audio est envoyé dans le vocal de CETTE guilde : l'utilisateur doit en être membre.
        # Sans ça, un guild_id forgé dans la requête (ou le filet "un seul vocal connecté" de
        # resolve_guild_id) permettrait de faire jouer des sons dans le vocal d'une guilde étrangère.
        visible = await self.resolve_visible_guild_ids(int(user_id))
        if str(guild_id) not in visible:
            return web.json_response(
                {"error": "Vous n'êtes pas membre du serveur Discord ciblé."},
                status=403,
            )

        vc = self.voice_clients_map.get(guild_id)
        mixer = self.mixers.get(guild_id)
        if vc is None or not vc.is_connected() or mixer is None:
            return web.json_response({"error": "Le bot n'est connecté à aucun salon vocal sur ce serveur (utilisez /join)."}, status=409)

        sound_id = data.get("id")
        volume = float(data.get("volume", 1.0))

        entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
        if entry is None:
            return web.json_response({"error": f"son introuvable : {sound_id}"}, status=404)

        # Visibilité du son sur TOUTES les guildes de l'utilisateur (`visible`, calculé plus haut),
        # pas seulement celle où il se trouve en vocal : un membre de plusieurs guildes peut jouer
        # n'importe lequel de ses sons dans le salon où il est, sans partage explicite préalable.
        if not self.sound_visible_to(entry, visible):
            return web.json_response(
                {"error": "Ce son n'appartient à aucune de vos guildes Discord connues."},
                status=403,
            )

        file_path = SOUNDS_DIR / f"{sound_id}{entry['extension']}"
        if not file_path.exists():
            return web.json_response({"error": "fichier manquant sur le serveur"}, status=404)

        # Plafonds de ressources de l'hébergeur : vérifiés AVANT l'anti-spam (un refus ne doit pas compter dans le quota).
        capacity_error = self._play_capacity_error(sound_id, guild_id, mixer)
        if capacity_error is not None:
            return web.json_response({"error": capacity_error, "retry_after": 2}, status=429, headers={"Retry-After": "2"})

        # Anti-spam réglé par l'admin de la guilde ciblée (où le son sera réellement joué) ;
        # les admins en sont exemptés. Vérifié en dernier : une requête refusée plus haut ne compte pas.
        retry_after = self._check_play_rate(guild_id, str(user_id))
        if retry_after is not None:
            return web.json_response({
                "error": f"Trop de sons envoyés : réessayez dans {retry_after} s.",
                "retry_after": retry_after,
            }, status=429)

        # On AJOUTE au mixeur plutôt que de remplacer la lecture en cours : plusieurs sons
        # (déclenchés par le même utilisateur ou des utilisateurs différents) se superposent
        # au lieu de s'annuler mutuellement.
        before_options, options = trim_ffmpeg_options(entry)
        source = discord.FFmpegPCMAudio(str(file_path), before_options=before_options, options=options)
        source = discord.PCMVolumeTransformer(source, volume=min(max(volume, 0.0), 2.0))

        # Le son part D'ABORD ; la résolution du membre Discord (highlight/avatar) — un appel
        # réseau si non déjà en cache — se fait ENSUITE en tâche de fond, pour ne jamais
        # retarder la lecture elle-même.
        str_user_id = str(user_id) if user_id else None
        on_finish = (lambda: self.clear_activity(sound_id, str_user_id)) if str_user_id else None

        mixer.add(sound_id, source, on_finish=on_finish)

        if str_user_id:
            asyncio.create_task(self._record_play_activity(sound_id, str_user_id, guild_id))

        return web.json_response({"status": "ok"})

    def _check_play_rate(self, guild_id: int, user_id: str) -> Optional[int]:
        """Anti-spam à la lecture : None si ce son peut partir (et le comptabilise), sinon le
        nombre de secondes à attendre. Fenêtre glissante de 60 s par (guilde, utilisateur) ;
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
        if len(history) >= limit:
            return max(1, int(60 - (now - history[0]) + 0.999))
        history.append(now)
        return None

    async def _record_play_activity(self, sound_id: str, user_id: str, guild_id: int) -> None:
        """Résout le membre et enregistre l'activité partagée (highlight/avatar) en arrière-plan,
        après que le son a déjà été lancé — voir _handle_play."""
        try:
            member = await self.find_member_async(int(user_id)) if user_id.isdigit() else None
            username = member.display_name if member else None
            avatar_url = str(member.display_avatar.url) if member else None
            self.record_activity(sound_id, user_id, username, avatar_url, guild_id)
            guild = self.get_guild(guild_id)
            record_play_stat(sound_id, user_id, username, avatar_url, guild_id, guild.name if guild else None)
        except Exception:
            log.exception("Échec de l'enregistrement d'activité en arrière-plan")

    async def _handle_stop_all(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        try:
            data = await request.json()
        except Exception:
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

        mixer.clear()  # déclenche aussi les callbacks on_finish : l'activité est effacée immédiatement
        return web.json_response({"status": "ok"})

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
        await interaction.response.send_message("Vous devez d'abord être connecté à un salon vocal.", ephemeral=True)
        return

    channel = member.voice.channel
    try:
        await bot.join_guild_voice(interaction.guild_id, channel)
    except CapacityError as ex:
        await interaction.response.send_message(f"⚠️ {ex}", ephemeral=True)
        return

    await interaction.response.send_message(
        f"Connecté à **{channel.name}**. Prêt à jouer les sons envoyés par WaseBoard.",
        ephemeral=True
    )


@bot.tree.command(name="leave", description="Déconnecte le bot du salon vocal")
async def leave(interaction: discord.Interaction) -> None:
    left = await bot.leave_guild_voice(interaction.guild_id)
    await interaction.response.send_message(
        "Déconnecté du salon vocal." if left else "Je ne suis connecté à aucun salon vocal ici.",
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
    await interaction.channel.send(
        "**WaseBoard**\n\n"
        "**Démarrer :** téléchargez l'application et connectez-vous avec Discord — ça suffit, elle se "
        "connecte automatiquement à l'instance publique par défaut. Un **autre** serveur WaseBoard "
        "(auto-hébergé) ? Utilisez « Connexion directe à ce serveur ».\n\n"
        "**Une fois connecté :** rejoignez un salon vocal puis cliquez 🔊, ou cliquez directement un son "
        "dans l'application.\n\n"
        "**Pour aller plus loin :** ajoutez ce bot à un autre serveur Discord (bibliothèque de sons "
        "indépendante), ou retrouvez le site, la communauté et le code source ci-dessous.",
        view=BotSetupView(),
    )
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
