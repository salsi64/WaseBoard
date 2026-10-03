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
