"""
Serveur WaseBoard : catalogue de sons partagé (API HTTP) + bot Discord qui les joue dans le
salon vocal. Peut être connecté à plusieurs serveurs Discord (guilds) en même temps, chacun
avec sa propre connexion vocale et son propre mixeur audio.

Voir README.md pour la mise en place complète.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
import discord
import numpy as np
from discord.ext import commands
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("waseboard-server")

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
SOUNDS_DIR = BASE_DIR / "sounds_data"
CATALOG_PATH = SOUNDS_DIR / "catalog.json"
SHARED_CATEGORIES_PATH = BASE_DIR / "shared_categories.json"
OAUTH_SESSIONS_PATH = BASE_DIR / "oauth_sessions.json"
STATS_PATH = BASE_DIR / "stats.jsonl"

DISCORD_API_BASE = "https://discord.com/api/v10"
# Marge de sécurité avant l'expiration réelle du token Discord sous-jacent : couvre la
# latence du round-trip de rafraîchissement, pas un éventuel décalage d'horloge (le calcul
# d'expiration est toujours relatif à time.time() local, jamais à une valeur absolue
# fournie par Discord — aucun risque de désynchronisation d'horloge possible).
DISCORD_TOKEN_EXPIRY_SKEW_SECONDS = 60

if not CONFIG_PATH.exists():
    raise SystemExit(
        "config.json introuvable. Copiez config.example.json vers config.json "
        "et renseignez au minimum votre bot_token (voir README.md)."
    )

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

BOT_TOKEN: str = CONFIG["bot_token"]
GUILD_ID: Optional[int] = CONFIG.get("guild_id")
HTTP_HOST: str = CONFIG.get("http_host", "0.0.0.0")
HTTP_PORT: int = CONFIG.get("http_port", 5005)
SHARED_SECRET: str = CONFIG.get("shared_secret", "")
OAUTH2_CLIENT_ID: str = CONFIG.get("oauth2_client_id", "")
OAUTH2_CLIENT_SECRET: str = CONFIG.get("oauth2_client_secret", "")

SOUNDS_DIR.mkdir(exist_ok=True)

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


def save_catalog(catalog: list[dict]) -> None:
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def load_shared_categories() -> dict:
    if not SHARED_CATEGORIES_PATH.exists():
        return {}
    with open(SHARED_CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_shared_categories(data: dict) -> None:
    with open(SHARED_CATEGORIES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_oauth_sessions() -> dict:
    if not OAUTH_SESSIONS_PATH.exists():
        return {}
    with open(OAUTH_SESSIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_oauth_sessions(data: dict) -> None:
    # Écriture atomique (temp + os.replace) : contrairement à shared_categories.json (écrit
    # seulement sur action utilisateur explicite), ce fichier est réécrit à chaque connexion
    # ET à chaque rafraîchissement paresseux de token — un crash en plein write ne doit
    # jamais laisser un JSON tronqué (ce fichier contient des jetons Discord vivants).
    tmp_path = OAUTH_SESSIONS_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, OAUTH_SESSIONS_PATH)


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
        self._activity_lock = threading.Lock()
        self._web_runner: Optional[web.AppRunner] = None

        # Sessions OAuth2 Discord : session_token (émis par WaseBoard) -> identité vérifiée +
        # jetons Discord. Chargées depuis disque pour survivre à un redémarrage.
        self.oauth_sessions: dict[str, dict] = load_oauth_sessions()
        self._session_refresh_locks: dict[str, asyncio.Lock] = {}
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Commandes slash synchronisées sur le serveur %s (instantané).", GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Commandes slash synchronisées globalement (peut prendre jusqu'à 1h à apparaître).")

        await self._start_http_server()

    async def close(self) -> None:
        if self.http_session is not None:
            await self.http_session.close()
        await super().close()

    # ---------- Salons vocaux ----------

    async def join_guild_voice(self, guild_id: int, channel: discord.VoiceChannel) -> discord.VoiceClient:
        existing = self.voice_clients_map.get(guild_id)
        if existing is not None and existing.is_connected():
            await existing.move_to(channel)
            return existing

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

    async def find_all_guilds_for_user(self, user_id: int) -> list[discord.Guild]:
        """Retrouve TOUS les serveurs Discord dont cet utilisateur est membre — chacun aura
        automatiquement sa propre catégorie partagée, indépendamment de toute présence vocale."""
        found: list[discord.Guild] = []
        for guild in self.guilds:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    member = None
            if member is not None:
                found.append(guild)
        return found

    async def resolve_visible_guild_ids(self, user_id: int) -> set[str]:
        """Guildes (en str) dont cet utilisateur est membre, parmi celles où le bot est présent."""
        guilds = await self.find_all_guilds_for_user(user_id)
        return {str(g.id) for g in guilds}

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
                    "client_id": OAUTH2_CLIENT_ID,
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
        app = web.Application(client_max_size=64 * 1024 * 1024)  # 64 Mo max par upload
        app.router.add_get("/sounds", self._handle_list_sounds)
        app.router.add_get("/sounds/{id}/file", self._handle_get_file)
        app.router.add_post("/sounds", self._handle_upload_sound)
        app.router.add_patch("/sounds/{id}", self._handle_update_sound)
        app.router.add_delete("/sounds/{id}", self._handle_delete_sound)
        app.router.add_post("/play", self._handle_play)
        app.router.add_post("/stop", self._handle_stop_all)
        app.router.add_post("/join-my-channel", self._handle_join_my_channel)
        app.router.add_get("/status", self._handle_status)
        app.router.add_get("/activity", self._handle_activity)
        app.router.add_get("/my-guilds", self._handle_my_guilds)
        app.router.add_get("/shared-categories", self._handle_get_shared_categories)
        app.router.add_post("/shared-categories/sounds", self._handle_shared_category_sound)
        app.router.add_get("/oauth/client-id", self._handle_oauth_client_id)
        app.router.add_post("/oauth/exchange", self._handle_oauth_exchange)

        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, HTTP_HOST, HTTP_PORT)
        await site.start()
        log.info("Serveur HTTP prêt sur %s:%s (catalogue + ordres de lecture WaseBoard).", HTTP_HOST, HTTP_PORT)

    def _check_auth(self, request: web.Request) -> bool:
        if not SHARED_SECRET:
            return True
        return request.headers.get("X-WaseBoard-Token") == SHARED_SECRET

    # ---------- Catalogue de sons ----------

    async def _handle_list_sounds(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        visible = await self.resolve_visible_guild_ids(int(session["user_id"]))
        return web.json_response({"sounds": self.catalog_visible_to(load_catalog(), visible)})

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

            guilds = await self.find_all_guilds_for_user(int(session["user_id"]))
            if not any(str(g.id) == raw_guild_id for g in guilds):
                return web.json_response({"error": "vous n'êtes pas membre de ce serveur Discord"}, status=403)

            original_filename = getattr(file_field, "filename", "") or ""
            extension = Path(original_filename).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                return web.json_response({"error": f"extension non supportée : {extension}"}, status=400)

            sound_id = uuid.uuid4().hex
            destination = SOUNDS_DIR / f"{sound_id}{extension}"

            # Hash du contenu (calculé ici, jamais fourni par le client) : sert de base à la
            # détection de doublons côté client, qui compare son fichier final (après découpe)
            # au hash de chaque son déjà présent dans le catalogue avant d'uploader.
            file_bytes = file_field.file.read()
            content_hash = hashlib.sha256(file_bytes).hexdigest()

            with open(destination, "wb") as out_file:
                out_file.write(file_bytes)

            catalog = load_catalog()
            entry = {"id": sound_id, "name": name, "extension": extension, "hash": content_hash, "guild_id": raw_guild_id}
            catalog.append(entry)
            save_catalog(catalog)

            log.info("Son ajouté : %s (%s)", name, sound_id)
            return web.json_response(entry)
        except Exception as ex:
            log.exception("Échec de l'upload")
            return web.json_response({"error": str(ex)}, status=500)

    async def _handle_update_sound(self, request: web.Request) -> web.Response:
        """PATCH /sounds/{id} : renomme et/ou change l'emoji d'un son (partages entre tous les
        utilisateurs, contrairement aux favoris/categories/volume/raccourcis, locaux)."""
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

        catalog = load_catalog()
        entry = next((s for s in catalog if s["id"] == sound_id), None)
        if entry is None:
            return web.json_response({"error": "son introuvable"}, status=404)

        visible = await self.resolve_visible_guild_ids(int(session["user_id"]))
        if not self.sound_visible_to(entry, visible):
            return web.json_response({"error": "son introuvable"}, status=404)

        if "name" not in data and "emoji" not in data:
            return web.json_response({"error": "'name' ou 'emoji' requis"}, status=400)

        if "name" in data:
            new_name = str(data.get("name", "")).strip()
            if not new_name:
                return web.json_response({"error": "'name' ne peut pas etre vide"}, status=400)
            entry["name"] = new_name

        if "emoji" in data:
            entry["emoji"] = str(data.get("emoji") or "").strip()

        save_catalog(catalog)
        return web.json_response(entry)

    async def _handle_delete_sound(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        session, err = await self._require_session(request)
        if err is not None:
            return err

        sound_id = request.match_info["id"]
        catalog = load_catalog()
        entry = next((s for s in catalog if s["id"] == sound_id), None)
        if entry is None:
            return web.json_response({"error": "son introuvable"}, status=404)

        visible = await self.resolve_visible_guild_ids(int(session["user_id"]))
        if not self.sound_visible_to(entry, visible):
            return web.json_response({"error": "son introuvable"}, status=404)

        file_path = SOUNDS_DIR / f"{sound_id}{entry['extension']}"
        if file_path.exists():
            file_path.unlink()

        catalog = [s for s in catalog if s["id"] != sound_id]
        save_catalog(catalog)

        shared = load_shared_categories()
        changed = False
        for sound_ids in shared.values():
            if sound_id in sound_ids:
                sound_ids.remove(sound_id)
                changed = True
        if changed:
            save_shared_categories(shared)

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

        guilds = await self.find_all_guilds_for_user(int(session["user_id"]))
        return web.json_response({
            "guilds": [
                {"id": str(g.id), "name": g.name, "icon_url": str(g.icon.url) if g.icon else None}
                for g in guilds
            ]
        })

    async def _handle_oauth_client_id(self, request: web.Request) -> web.Response:
        """Renvoie l'identifiant client OAuth2 Discord de cette instance — pas un secret,
        mais propre à chaque déploiement auto-hébergé, donc le client ne peut pas le
        connaître à l'avance."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not OAUTH2_CLIENT_ID:
            return web.json_response({"error": "oauth2_client_id non configuré côté serveur"}, status=500)
        return web.json_response({"client_id": OAUTH2_CLIENT_ID})

    async def _handle_oauth_exchange(self, request: web.Request) -> web.Response:
        """Échange un code d'autorisation Discord contre une session WaseBoard. Le
        client_secret Discord reste ici, jamais transmis au client."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not OAUTH2_CLIENT_ID or not OAUTH2_CLIENT_SECRET:
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
                "client_id": OAUTH2_CLIENT_ID,
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

        guilds = await self.find_all_guilds_for_user(int(user_id))
        if not any(str(g.id) == guild_id for g in guilds):
            return web.json_response({"error": "vous n'êtes pas membre de ce serveur Discord"}, status=403)

        if action == "add":
            visible = await self.resolve_visible_guild_ids(int(user_id))
            entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
            if entry is None or not self.sound_visible_to(entry, visible):
                return web.json_response({"error": "son introuvable"}, status=404)

        shared = load_shared_categories()
        sound_ids = shared.setdefault(guild_id, [])

        if action == "add" and sound_id not in sound_ids:
            sound_ids.append(sound_id)
        elif action == "remove" and sound_id in sound_ids:
            sound_ids.remove(sound_id)

        save_shared_categories(shared)
        return web.json_response({"sound_ids": sound_ids})

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

        vc = self.voice_clients_map.get(guild_id)
        mixer = self.mixers.get(guild_id)
        if vc is None or not vc.is_connected() or mixer is None:
            return web.json_response({"error": "Le bot n'est connecté à aucun salon vocal sur ce serveur (utilisez /join)."}, status=409)

        sound_id = data.get("id")
        volume = float(data.get("volume", 1.0))

        entry = next((s for s in load_catalog() if s["id"] == sound_id), None)
        if entry is None:
            return web.json_response({"error": f"son introuvable : {sound_id}"}, status=404)

        # Visibilité sur TOUTES les guildes de l'utilisateur, pas seulement celle où il se trouve
        # actuellement en vocal : un utilisateur membre de plusieurs guildes doit pouvoir jouer
        # n'importe lequel de ses sons dans le salon où il est, sans avoir à le partager
        # explicitement vers chaque guilde au préalable.
        visible = await self.resolve_visible_guild_ids(int(user_id))
        if not self.sound_visible_to(entry, visible):
            return web.json_response(
                {"error": "Ce son n'appartient à aucune de vos guildes Discord connues."},
                status=403,
            )

        file_path = SOUNDS_DIR / f"{sound_id}{entry['extension']}"
        if not file_path.exists():
            return web.json_response({"error": "fichier manquant sur le serveur"}, status=404)

        # On AJOUTE au mixeur plutôt que de remplacer la lecture en cours : plusieurs sons
        # (déclenchés par le même utilisateur ou des utilisateurs différents) se superposent
        # au lieu de s'annuler mutuellement.
        source = discord.FFmpegPCMAudio(str(file_path))
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
        await self.join_guild_voice(guild_id, channel)

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
    await bot.join_guild_voice(interaction.guild_id, channel)

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


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
