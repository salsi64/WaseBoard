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
}


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

    if "guild_id" in config:
        try:
            config["guild_id"] = _optional_int(config["guild_id"])
        except (TypeError, ValueError) as ex:
            raise ConfigError(f"guild_id invalide ({config['guild_id']!r}) : ce doit être l'identifiant numérique "
                              "du serveur Discord.") from ex
    return config
