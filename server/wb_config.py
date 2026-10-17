"""Chargement de la configuration du serveur WaseBoard : `config.json` + variables d'environnement.

Les variables d'environnement `WASEBOARD_*` l'emportent sur `config.json` (pratique en conteneur : tout
tient dans un fichier `.env`, aucun fichier de configuration à monter). `config.json` reste pris en charge
tel quel — une installation existante n'a rien à changer.

`WASEBOARD_DATA_DIR` déplace TOUTES les données (config.json compris, catalogue, sons, sessions, statistiques...)
hors du dossier du script ; par défaut, elles restent à côté de `server.py` comme avant.
"""

import json
import os
from pathlib import Path
from typing import Callable, Mapping, Optional

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("WASEBOARD_DATA_DIR") or BASE_DIR)
CONFIG_PATH = DATA_DIR / "config.json"


class ConfigError(Exception):
    """Configuration illisible ou invalide (message destiné à l'administrateur, en français)."""


def _optional_int(value) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return int(value)


def _boolean(value) -> bool:
    """Variable d'environnement booléenne : 1/true/yes/on ou 0/false/no/off (sans tenir compte de la casse)."""
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "oui"):
        return True
    if text in ("0", "false", "no", "off", "non"):
        return False
    raise ValueError("« true » ou « false » est attendu")


def _json_object(value) -> dict:
    """Variable d'environnement contenant un objet JSON (ex : {"max_total_mb": 500})."""
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("un objet JSON {...} est attendu")
    return parsed


# Réglages de guilde auxquels l'hébergeur peut imposer des valeurs par défaut ou des plafonds (0 = illimité).
# max_sources_per_guild en fait partie (PAS un plafond d'instance global) : chaque guilde peut avoir son propre
# plafond de sons simultanés, ou aucun — voir DEFAULT_GUILD_SETTINGS/default_guild_limits dans server.py.
GUILD_LIMIT_KEYS = ("max_sounds", "max_file_mb", "max_duration_s", "max_total_mb", "play_rate_per_min",
                    "max_sources_per_guild")
# Plafonds de ressources de l'instance (0 = désactivé) : voir « Capacité » dans le README.
RESOURCE_CAP_KEYS = ("max_guilds", "max_concurrent_voice", "max_ffmpeg_processes", "http_rate_limit_per_min")


# clé de config.json -> (variable d'environnement, conversion)
ENV_OVERRIDES: dict[str, tuple[str, Callable]] = {
    "bot_token": ("WASEBOARD_BOT_TOKEN", str),
    "guild_id": ("WASEBOARD_GUILD_ID", _optional_int),
    "http_host": ("WASEBOARD_HTTP_HOST", str),
    "http_port": ("WASEBOARD_HTTP_PORT", int),
    "shared_secret": ("WASEBOARD_SHARED_SECRET", str),
    "oauth2_client_id": ("WASEBOARD_OAUTH2_CLIENT_ID", str),
    "oauth2_client_secret": ("WASEBOARD_OAUTH2_CLIENT_SECRET", str),
    "public_url": ("WASEBOARD_PUBLIC_URL", str),
    "download_url": ("WASEBOARD_DOWNLOAD_URL", str),
    "trash_retention_days": ("WASEBOARD_TRASH_RETENTION_DAYS", int),
    "max_guilds": ("WASEBOARD_MAX_GUILDS", int),
    "max_concurrent_voice": ("WASEBOARD_MAX_CONCURRENT_VOICE", int),
    "max_ffmpeg_processes": ("WASEBOARD_MAX_FFMPEG_PROCESSES", int),
    "http_rate_limit_per_min": ("WASEBOARD_HTTP_RATE_LIMIT_PER_MIN", int),
    "voice_idle_sleep_s": ("WASEBOARD_VOICE_IDLE_SLEEP_S", float),
    "pcm_cache_mb": ("WASEBOARD_PCM_CACHE_MB", float),
    "session_max_idle_days": ("WASEBOARD_SESSION_MAX_IDLE_DAYS", float),
    "loop_max_minutes": ("WASEBOARD_LOOP_MAX_MINUTES", float),
    "alerts_enabled": ("WASEBOARD_ALERTS_ENABLED", _boolean),
    "alerts_user_id": ("WASEBOARD_ALERTS_USER_ID", str),
    "alerts_disk_percent": ("WASEBOARD_ALERTS_DISK_PERCENT", int),
    "alerts_cert_days": ("WASEBOARD_ALERTS_CERT_DAYS", int),
    "alerts_error_burst": ("WASEBOARD_ALERTS_ERROR_BURST", int),
    "default_guild_limits": ("WASEBOARD_DEFAULT_GUILD_LIMITS", _json_object),
    "guild_limit_ceilings": ("WASEBOARD_GUILD_LIMIT_CEILINGS", _json_object),
}


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_limits(config: dict) -> None:
    """Refuse au démarrage (avec un message précis) une limite mal écrite plutôt que de l'ignorer en silence."""
    for key in RESOURCE_CAP_KEYS:
        if key in config and not _is_count(config[key]):
            raise ConfigError(f"{key} doit être un entier positif ou nul (0 = désactivé), pas {config[key]!r}.")
    if "voice_idle_sleep_s" in config:
        value = config["voice_idle_sleep_s"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 3600:
            raise ConfigError(f"voice_idle_sleep_s doit être un nombre de secondes entre 0 (jamais) et 3600, pas {value!r}.")
    if "loop_max_minutes" in config:
        value = config["loop_max_minutes"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 720:
            raise ConfigError(f"loop_max_minutes doit être un nombre de minutes entre 0 (boucles désactivées) et 720, pas {value!r}.")
    if "session_max_idle_days" in config:
        value = config["session_max_idle_days"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 3650:
            raise ConfigError(f"session_max_idle_days doit être un nombre de jours entre 0 (jamais) et 3650, pas {value!r}.")
    if "pcm_cache_mb" in config:
        value = config["pcm_cache_mb"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 8192:
            raise ConfigError(f"pcm_cache_mb doit être une quantité de mémoire en Mo entre 0 (désactivé) et 8192, pas {value!r}.")
    if "alerts_enabled" in config and not isinstance(config["alerts_enabled"], bool):
        raise ConfigError(f"alerts_enabled doit être true ou false, pas {config['alerts_enabled']!r}.")
    if "alerts_user_id" in config:
        value = config["alerts_user_id"]
        if value not in (None, "") and not str(value).isdigit():
            raise ConfigError(f"alerts_user_id doit être l'identifiant numérique d'un compte Discord (ou rester vide : le propriétaire "
                              f"de l'application), pas {value!r}.")
    for key, low, high, hint in (("alerts_disk_percent", 50, 99, "pourcentage de disque rempli (0 = alerte désactivée)"),
                                 ("alerts_cert_days", 1, 90, "jours avant l'expiration du certificat (0 = alerte désactivée)"),
                                 ("alerts_error_burst", 3, 1000, "nombre d'erreurs en 5 minutes (0 = alerte désactivée)")):
        if key in config:
            value = config[key]
            if isinstance(value, bool) or not isinstance(value, int) or not (value == 0 or low <= value <= high):
                raise ConfigError(f"{key} doit valoir 0 ou être un entier entre {low} et {high} : {hint}, pas {value!r}.")
    for name in ("default_guild_limits", "guild_limit_ceilings"):
        value = config.get(name)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ConfigError(f"{name} doit être un objet JSON, par exemple {{\"max_total_mb\": 500}}.")
        for key, number in value.items():
            if key not in GUILD_LIMIT_KEYS:
                raise ConfigError(f"{name} : « {key} » est inconnu (valeurs possibles : {', '.join(GUILD_LIMIT_KEYS)}).")
            if not _is_count(number):
                raise ConfigError(f"{name}.{key} doit être un entier positif ou nul (0 = illimité), pas {number!r}.")


def load_config(environ: Optional[Mapping[str, str]] = None, config_path: Optional[Path] = None) -> dict:
    """Renvoie la configuration fusionnée (config.json puis variables d'environnement non vides).
    Un fichier absent n'est pas une erreur (tout peut venir de l'environnement) ; c'est à l'appelant de
    vérifier que le minimum — le jeton du bot — est présent."""
    env = os.environ if environ is None else environ
    path = CONFIG_PATH if config_path is None else config_path

    config: dict = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError) as ex:
            raise ConfigError(f"{path} est illisible ou n'est pas un JSON valide : {ex}") from ex
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} doit contenir un objet JSON ({{ ... }}).")
        config = loaded

    for key, (variable, convert) in ENV_OVERRIDES.items():
        raw = env.get(variable)
        if raw is None or not raw.strip():
            continue  # variable absente ou vide (ex. ligne laissée vide dans .env) : on garde config.json
        try:
            config[key] = convert(raw.strip())
        except ValueError as ex:
            raise ConfigError(f"La variable {variable} a une valeur invalide ({raw!r}) : {ex}") from ex

    validate_limits(config)

    if "guild_id" in config:
        try:
            config["guild_id"] = _optional_int(config["guild_id"])
        except (TypeError, ValueError) as ex:
            raise ConfigError(f"guild_id invalide ({config['guild_id']!r}) : ce doit être l'identifiant numérique "
                              "du serveur Discord.") from ex
    return config
