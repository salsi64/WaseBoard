"""Application web (PWA) de WaseBoard, servie par le serveur sous /app : mêmes sons, même bot, depuis un navigateur ou un
téléphone (« ajouter à l'écran d'accueil »), sans rien installer.

Ce module ne fait que SERVIR la page (fichiers du dossier web/) et dire au navigateur comment se connecter à Discord ;
l'API qu'elle appelle est celle de l'application Windows (server.py), authentifiée par la session Discord de la personne.

Activité Discord : la même page, chargée dans Discord (iframe servie par le proxy de Discord sous
`https://<identifiant client>.discordsays.com`) — voir activity.js côté page. Elle se reconnaît à `frame_id` dans l'adresse ; seule cette
réponse HTML perd l'interdiction d'être affichée dans un cadre (frame-ancestors), tout le reste de la politique de sécurité reste.

Principes de sécurité :
 - la page est publique mais ne contient aucun secret (le « secret partagé » de l'instance n'y figure pas : une page
   publique ne peut pas en garder un). Ce qui authentifie ses appels, c'est la session Discord obtenue par OAuth2, présentée
   dans un en-tête `Authorization: Bearer` (pas de cookie, donc pas de CSRF) ;
 - politique de sécurité de contenu stricte (aucun script ni style en ligne, une seule origine) : un texte injecté dans la
   page — nom de son, pseudo — ne peut pas s'exécuter ;
 - seuls les fichiers présents dans web/ au démarrage sont servis, jamais un chemin construit à partir de la requête.
"""

import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

from aiohttp import web

log = logging.getLogger("waseboard-webapp")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

#: Fichiers dont le contenu reçoit l'empreinte de la version de la page (voir WebApp.build_id) : le service worker change
#: ainsi dès qu'un fichier de la page change, ce qui déclenche la mise à jour chez les gens qui l'ont installée.
BUILD_PLACEHOLDER = b"__BUILD__"
BUILD_REPLACED = {".html", ".js", ".mjs", ".webmanifest"}

#: Une seule origine, aucun script ni style en ligne. Les avatars et icônes de serveurs viennent du CDN de Discord.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://cdn.discordapp.com data:; "
       "connect-src 'self'; manifest-src 'self'; worker-src 'self'; media-src 'self' blob:; object-src 'none'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

#: Même politique pour la page affichée dans Discord, sans « frame-ancestors » : le cadre est celui de Discord (client web, bureau, mobile).
ACTIVITY_CSP = CSP.replace("; frame-ancestors 'none'", "")
ACTIVITY_HOST_SUFFIX = ".discordsays.com"

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    # L'adresse de retour de Discord contient un code d'autorisation à usage unique : elle ne doit jamais fuiter en Referer.
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

SCOPE = "identify"


@dataclass(frozen=True)
class Asset:
    body: bytes
    content_type: str
    etag: str


def origin_of(url: str) -> Optional[str]:
    """« https://hôte[:port] » d'une adresse, ou None si ce n'est pas une adresse http(s) complète."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".lower()


class WebApp:
    def __init__(self, web_dir: Path, public_url: str, extra_redirect_uris: Iterable[str] = (),
                 client_id: Callable[[], str] = lambda: "", version: str = "", features: Optional[list] = None,
                 activity: bool = True, invite_url: Callable[[], str] = lambda: "") -> None:
        self.web_dir = web_dir
        self.public_url = (public_url or "").rstrip("/")
        self.extra_redirect_uris = [u for u in extra_redirect_uris if origin_of(u)]
        self._client_id, self.version, self.features = client_id, version, features if features is not None else []
        self._invite_url = invite_url
        self.activity = activity
        self._refused_origins: set = set()
        self.assets: dict[str, Asset] = {}
        self.build_id = ""

    # ---------- Chargement ----------

    def load(self) -> bool:
        """Lit les fichiers de la page. Faux (et la page reste désactivée) sans adresse publique valide ou sans index.html."""
        self.assets, self.build_id = {}, ""
        if origin_of(self.public_url) is None:
            log.warning("Application web désactivée : public_url (adresse publique du serveur) est absent ou invalide — "
                        "Discord a besoin d'une adresse de retour complète pour la connexion.")
            return False

        files = {}
        if self.web_dir.is_dir():
            for path in sorted(self.web_dir.rglob("*")):
                relative = path.relative_to(self.web_dir)
                if path.is_file() and path.suffix.lower() in CONTENT_TYPES and not any(part.startswith(".") for part in relative.parts):
                    files[relative.as_posix()] = path.read_bytes()
        if "index.html" not in files:
            log.warning("Application web désactivée : %s/index.html est introuvable.", self.web_dir)
            return False

        digest = hashlib.sha256()
        for name, body in files.items():
            digest.update(name.encode("utf-8") + b"\0" + body + b"\0")
        self.build_id = digest.hexdigest()[:12]

        for name, body in files.items():
            suffix = Path(name).suffix.lower()
            if suffix in BUILD_REPLACED:
                body = body.replace(BUILD_PLACEHOLDER, self.build_id.encode("ascii"))
            self.assets[name] = Asset(body, CONTENT_TYPES.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream",
                                      '"' + hashlib.sha256(body).hexdigest()[:20] + '"')
        log.info("Application web prête sur %s/app/ (%d fichiers, version %s).", self.public_url, len(self.assets), self.build_id)
        return True

    @property
    def enabled(self) -> bool:
        return "index.html" in self.assets

    # ---------- Adresses autorisées ----------

    def callback_uri(self) -> str:
        return f"{self.public_url}/app/callback"

    def redirect_uris(self) -> set[str]:
        """Adresses de retour acceptées pour l'échange du code Discord (à déclarer aussi au portail Discord)."""
        return {self.callback_uri(), *self.extra_redirect_uris}

    def activity_origin(self) -> Optional[str]:
        """Origine de la page quand elle est affichée dans Discord : https://<identifiant client>.discordsays.com."""
        client_id = self._client_id()
        return f"https://{client_id}{ACTIVITY_HOST_SUFFIX}" if self.activity and client_id else None

    def is_activity_origin(self, origin: Optional[str]) -> bool:
        wanted = self.activity_origin()
        return bool(origin) and wanted is not None and origin.lower().rstrip("/") == wanted

    def allowed_origins(self) -> set[str]:
        origins = {origin_of(self.public_url), self.activity_origin()}
        origins.update(origin_of(u) for u in self.extra_redirect_uris)
        return {o for o in origins if o}

    def note_refused_origin(self, origin: Optional[str]) -> None:
        """Journalise une fois chaque origine refusée : sans ça, une origine inattendue (proxy qui réécrit l'en-tête, nom de
        domaine oublié dans web_redirect_uris) ne se voit que par des erreurs 401 muettes."""
        if origin and origin not in self._refused_origins and len(self._refused_origins) < 20:
            self._refused_origins.add(origin)
            log.warning("Application web : appel refusé, origine « %s » non autorisée (voir web_redirect_uris dans le README).", origin[:120])

    def origin_allowed(self, origin: Optional[str]) -> bool:
        """Un appel venu d'un navigateur porte `Origin` quand il n'est pas une simple lecture de la même origine : absent =
        lecture de même origine (ou client qui n'est pas un navigateur, protégé de toute façon par sa session)."""
        if origin is None:
            return True
        normalized = origin_of(origin)
        return normalized is not None and normalized == origin.lower().rstrip("/") and normalized in self.allowed_origins()

    # ---------- Routes ----------

    def add_routes(self, router: web.UrlDispatcher) -> None:
        router.add_get("/", self._root)                         # Activité Discord : la racine du domaine est chargée avec ?frame_id=…
        router.add_get("/app", self._redirect_to_app)
        router.add_get("/app/", self._index)
        router.add_get("/app/callback", self._index)            # retour de Discord : la page lit `code` et `state`
        router.add_get("/app/api/config", self._config)
        router.add_get("/app/{name:.+}", self._asset)

    def _respond(self, request: web.Request, asset: Asset, headers_override: Optional[dict] = None) -> web.Response:
        headers = {**SECURITY_HEADERS, **(headers_override or {}), "ETag": asset.etag, "Cache-Control": "no-cache"}
        if request.headers.get("If-None-Match") == asset.etag:
            return web.Response(status=304, headers=headers)
        media_type, _, charset = asset.content_type.partition("; charset=")
        return web.Response(body=asset.body, content_type=media_type, charset=charset or None, headers=headers)

    def _not_found(self) -> web.Response:
        return web.Response(status=404, text="Introuvable.", headers={**SECURITY_HEADERS, "Cache-Control": "no-store"})

    async def _redirect_to_app(self, request: web.Request) -> web.Response:
        if not self.enabled:
            return self._not_found()
        raise web.HTTPFound("/app/", headers=SECURITY_HEADERS)

    @staticmethod
    def _in_discord(request: web.Request) -> bool:
        """Chargée comme Activité : Discord ajoute frame_id (et instance_id…) à l'adresse de l'iframe."""
        return "frame_id" in request.query

    async def _index(self, request: web.Request) -> web.Response:
        asset = self.assets.get("index.html")
        if not asset:
            return self._not_found()
        override = {"Content-Security-Policy": ACTIVITY_CSP} if self.activity and self._in_discord(request) else None
        return self._respond(request, asset, override)

    async def _root(self, request: web.Request) -> web.Response:
        """GET / : rien à voir, sauf pour l'Activité Discord (la racine du domaine est ce que le proxy de Discord charge)."""
        if self.enabled and self.activity and self._in_discord(request):
            return await self._index(request)
        return self._not_found()

    async def _asset(self, request: web.Request) -> web.Response:
        asset = self.assets.get(request.match_info["name"])
        return self._respond(request, asset) if asset else self._not_found()

    async def _config(self, request: web.Request) -> web.Response:
        """Ce que la page doit savoir pour envoyer la personne se connecter à Discord. Rien de secret : l'identifiant client
        d'une application Discord est public (il figure dans l'adresse d'autorisation que voit tout le monde)."""
        headers = {**SECURITY_HEADERS, "Cache-Control": "no-store"}
        if not self.enabled:
            return web.json_response({"error": "disabled"}, status=404, headers=headers)
        client_id = self._client_id()
        if not client_id:
            return web.json_response({"error": "oauth_not_configured"}, status=503, headers=headers)
        return web.json_response({
            "client_id": client_id,
            "redirect_uri": self.callback_uri(),
            "scope": SCOPE,
            "activity": self.activity,
            "version": self.version,
            "build": self.build_id,
            "features": self.features,
            "invite_url": self._invite_url() or "",   # bouton « Ajouter le bot à mon serveur » des paramètres ; vide : pas de bouton
        }, headers=headers)
