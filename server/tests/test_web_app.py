"""Application web (PWA) sous /app : distribution des fichiers et en-têtes de sécurité (webapp.py), connexion Discord depuis
le navigateur (adresse de retour /app/callback, origine du site), et ce que ça change à l'authentification de l'API
(server.py : _check_auth, /oauth/exchange). Vrai code, vrai serveur HTTP de test (aiohttp), faux objets Discord.
Dossier de données isolé (_env.py)."""
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402
import webapp as W  # noqa: E402
import wb_config  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


PUBLIC = "https://waseboard.test"


INVITE = "https://discord.com/oauth2/authorize?client_id=555&permissions=3146752&scope=bot%20applications.commands"


def make_app(public=PUBLIC, extra=(), web_dir=None, client_id="555", activity=True, invite=INVITE):
    wa = W.WebApp(web_dir or S.WEB_DIR, public, extra, lambda: client_id, "9.9.9", ["web_app"], activity=activity, invite_url=lambda: invite)
    wa.load()
    return wa


async def serve(wa):
    app = web.Application()
    wa.add_routes(app.router)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class Req:
    """Faux objet requête de server.py (mêmes principes que test_security.py)."""
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self.query, self.match_info, self._body = {}, {}, body
    async def json(self):
        if self._body is None:
            raise ValueError("pas de corps")
        return self._body


async def main():
    print("\n--- Fichiers de la page ---")
    wa = make_app()
    check("la page se charge (index.html présent, adresse publique valide)", wa.enabled and wa.build_id and len(wa.build_id) == 12, wa.build_id)
    client = await serve(wa)
    try:
        r = await client.get("/app/")
        body = await r.text()
        check("/app/ : la page, en HTML", r.status == 200 and r.content_type == "text/html" and "<title>WaseBoard</title>" in body)
        check("empreinte de version injectée (plus de __BUILD__)", "__BUILD__" not in body and wa.build_id in body)
        for header in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Cross-Origin-Opener-Policy", "Permissions-Policy"):
            check(f"en-tête de sécurité {header}", header in r.headers, dict(r.headers))
        csp = r.headers["Content-Security-Policy"]
        check("CSP : une seule origine, aucun script ni style en ligne, pas de cadre, pas de plugin",
              "script-src 'self'" in csp and "style-src 'self'" in csp and "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
              and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "base-uri 'none'" in csp and "default-src 'self'" in csp, csp)
        check("CSP : avatars et icônes du CDN de Discord seulement, pour les images", "img-src 'self' https://cdn.discordapp.com" in csp)
        check("le code d'autorisation ne fuite pas en Referer", r.headers["Referrer-Policy"] == "no-referrer")
        check("toujours revalidé (mises à jour immédiates)", r.headers["Cache-Control"] == "no-cache" and r.headers.get("ETag"))

        r = await client.get("/app", allow_redirects=False)
        check("/app → /app/", r.status == 302 and r.headers["Location"] == "/app/")
        r = await client.get("/app/callback?code=abc&state=xyz")
        check("/app/callback : la même page (elle lit code et state)", r.status == 200 and "<title>WaseBoard</title>" in await r.text())

        types = {"app.js": "text/javascript", "logic.js": "text/javascript", "i18n.js": "text/javascript", "app.css": "text/css",
                 "manifest.webmanifest": "application/manifest+json", "sw.js": "text/javascript", "icons/icon-192.png": "image/png",
                 "icons/icon-512.png": "image/png", "icons/icon-maskable-512.png": "image/png", "icons/apple-touch-icon.png": "image/png"}
        for name, expected in types.items():
            r = await client.get(f"/app/{name}")
            data = await r.read()
            check(f"/app/{name} : {expected}", r.status == 200 and r.content_type == expected and len(data) > 100 and r.headers["X-Content-Type-Options"] == "nosniff",
                  (r.status, r.content_type))
        sw = await (await client.get("/app/sw.js")).text()
        check("service worker : version = empreinte des fichiers", f"const BUILD = '{wa.build_id}';" in sw and "__BUILD__" not in sw)
        manifest = json.loads(await (await client.get("/app/manifest.webmanifest")).text())
        check("manifeste valide, installable", manifest["start_url"] == "/app/" and manifest["scope"] == "/app/" and manifest["display"] == "standalone")

        r = await client.get("/app/app.js")
        etag = r.headers["ETag"]
        r2 = await client.get("/app/app.js", headers={"If-None-Match": etag})
        check("ETag : 304 sans corps quand rien n'a changé", r2.status == 304 and not await r2.read())
        r3 = await client.get("/app/app.js", headers={"If-None-Match": '"autre"'})
        check("ETag différent : fichier renvoyé", r3.status == 200)

        print("\n--- Rien d'autre que les fichiers de la page ---")
        for path in ("/app/nope.js", "/app/../server.py", "/app/%2e%2e/server.py", "/app/..%2fserver.py", "/app/icons/", "/app/.hidden",
                     "/app/icons/../../config.json", "/app/server.py", "/app/tests/x", "/app/index.html.bak", "/app/%00"):
            r = await client.get(path, allow_redirects=False)
            text = await r.text()
            check(f"{path} : introuvable, sans contenu du serveur", r.status in (404, 400) and "client_secret" not in text and "bot_token" not in text and "import " not in text, (r.status, text[:60]))
        r = await client.get("/app/index.html")
        check("index.html se sert aussi par son nom", r.status == 200)
        r = await client.post("/app/", data=b"x")
        check("POST sur la page : refusé", r.status in (404, 405))

        print("\n--- Configuration de connexion ---")
        r = await client.get("/app/api/config")
        cfg = await r.json()
        check("/app/api/config : identifiant client, adresse de retour, portée", cfg["client_id"] == "555" and cfg["redirect_uri"] == f"{PUBLIC}/app/callback"
              and cfg["scope"] == "identify" and cfg["version"] == "9.9.9" and cfg["build"] == wa.build_id, cfg)
        check("…sans aucun secret, jamais mise en cache", set(cfg) == {"client_id", "redirect_uri", "scope", "activity", "version", "build", "features", "invite_url"}
              and r.headers["Cache-Control"] == "no-store")
        check("adresse d'ajout du bot : celle fournie par le serveur (page publique de Discord, aucun secret)", cfg["invite_url"] == INVITE and cfg["invite_url"].startswith("https://discord.com/oauth2/authorize?"), cfg)
    finally:
        await client.close()

    wa_full = make_app(invite="")
    client = await serve(wa_full)
    try:
        r = await client.get("/app/api/config")
        check("instance pleine : adresse d'ajout vide (la page n'affiche pas le bouton)", (await r.json())["invite_url"] == "")
    finally:
        await client.close()

    wa2 = make_app(client_id="")
    client = await serve(wa2)
    try:
        r = await client.get("/app/api/config")
        check("OAuth2 non configuré : 503 « oauth_not_configured » (la page l'explique)", r.status == 503 and (await r.json())["error"] == "oauth_not_configured")
    finally:
        await client.close()

    print("\n--- Version de la page ---")
    tmp = _env.DATA_DIR / "webcopy"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(S.WEB_DIR, tmp)
    before = make_app(web_dir=tmp).build_id
    (tmp / "app.css").write_text((tmp / "app.css").read_text(encoding="utf-8") + "\n/* modifié */\n", encoding="utf-8")
    after = make_app(web_dir=tmp).build_id
    check("l'empreinte change dès qu'un fichier change (le service worker aussi : mise à jour automatique)", before != after and make_app(web_dir=tmp).build_id == after)
    (tmp / ".secret.js").write_text("x = 1", encoding="utf-8")
    (tmp / "notes.txt").write_text("brouillon", encoding="utf-8")
    (tmp / "icons" / "script.php").write_text("<?php ?>", encoding="utf-8")
    served = make_app(web_dir=tmp).assets
    check("fichiers cachés et extensions inconnues ignorés", ".secret.js" not in served and "notes.txt" not in served and "icons/script.php" not in served and "app.js" in served, sorted(served))
    (tmp / "index.html").unlink()
    check("sans index.html : page désactivée", not make_app(web_dir=tmp).enabled)
    shutil.rmtree(tmp, ignore_errors=True)
    check("dossier absent : page désactivée sans erreur", not make_app(web_dir=_env.DATA_DIR / "absent").enabled)
    for bad in ("", "ftp://x.org", "pas une adresse", "https://", "/relatif"):
        check(f"public_url « {bad} » : page désactivée (Discord exige une adresse de retour complète)", not make_app(public=bad).enabled)
    check("public_url avec « / » final : même adresse de retour", make_app(public=PUBLIC + "/").callback_uri() == f"{PUBLIC}/app/callback")

    print("\n--- Origines et adresses de retour ---")
    wa = make_app(extra=["https://autre.example/app/callback", "http://192.168.1.10:5005/app/callback", "pas-une-adresse"])
    check("adresses de retour : celle du site + les supplémentaires valides", wa.redirect_uris() == {f"{PUBLIC}/app/callback", "https://autre.example/app/callback", "http://192.168.1.10:5005/app/callback"}, wa.redirect_uris())
    allowed = [None, PUBLIC, PUBLIC.upper(), "https://autre.example", "http://192.168.1.10:5005"]
    refused = ["https://evil.example", "http://waseboard.test", "https://waseboard.test.evil.example", "https://evil.example/https://waseboard.test",
               PUBLIC + "/chemin", "null", "", "javascript:alert(1)", "https://waseboard.test:8443", "https://autre.example:1"]
    check("Origin acceptée : absente (lecture de même origine), celle du site, celles déclarées", all(wa.origin_allowed(o) for o in allowed), [o for o in allowed if not wa.origin_allowed(o)])
    check("Origin refusée : autre site, autre schéma ou port, avec chemin, « null », vide", not any(wa.origin_allowed(o) for o in refused), [o for o in refused if wa.origin_allowed(o)])

    print("\n--- Authentification de l'API par la page (server.py : _check_auth) ---")
    bot = S.bot
    S.SHARED_SECRET = "s" * 40
    S.OAUTH2_CLIENT_SECRET = "clientsecret"
    S.OAUTH2_CLIENT_ID = "555"
    bot.oauth_sessions.clear()
    bot.oauth_sessions["web-token-123456789012345678901234"] = {"user_id": "20", "username": "alice", "avatar_url": None,
                                                                  "discord_access_token": "x", "discord_refresh_token": "y",
                                                                  "discord_token_expires_at": time.time() + 10 ** 6, "created_at": 0, "last_used_at": 0}
    TOKEN = "web-token-123456789012345678901234"
    bearer = {"Authorization": f"Bearer {TOKEN}"}
    bot.web_app = None
    check("sans application web : secret exigé comme avant (une session seule ne suffit pas)", not bot._check_auth(Req(bearer)) and not bot._check_auth(Req({**bearer, "Origin": PUBLIC})))
    check("…et le secret partagé passe", bot._check_auth(Req({"X-WaseBoard-Token": S.SHARED_SECRET})))
    bot.web_app = make_app()
    check("page web : session valide, sans Origin (lecture de même origine)", bot._check_auth(Req(bearer)))
    check("page web : session valide + Origin du site", bot._check_auth(Req({**bearer, "Origin": PUBLIC})))
    check("page web : Origin d'un autre site refusée même avec une session valide", not bot._check_auth(Req({**bearer, "Origin": "https://evil.example"})))
    check("page web : jeton inconnu refusé", not bot._check_auth(Req({"Authorization": "Bearer inconnu", "Origin": PUBLIC})))
    check("page web : aucune session refusée (l'Origin seule ne donne rien)", not bot._check_auth(Req({"Origin": PUBLIC})) and not bot._check_auth(Req()))
    check("page web : en-tête Authorization qui n'est pas « Bearer » refusé", not bot._check_auth(Req({"Authorization": f"Basic {TOKEN}"})))
    check("le secret partagé continue de passer (application Windows)", bot._check_auth(Req({"X-WaseBoard-Token": S.SHARED_SECRET})))
    check("mauvais secret sans session : refusé", not bot._check_auth(Req({"X-WaseBoard-Token": "faux"})))
    S.SHARED_SECRET = ""
    check("instance sans secret : tout passe, comme avant", bot._check_auth(Req()))
    S.SHARED_SECRET = "s" * 40

    async def call(handler, headers=None, body=None):
        resp = await handler(Req(headers, body))
        try:
            payload = json.loads(resp.text)
        except Exception:
            payload = None
        return resp.status, payload

    async def fake_memberships(user_id):
        return []
    bot.find_memberships = fake_memberships
    st, p = await call(bot._handle_list_sounds, bearer)
    check("GET /sounds depuis la page : session seule, sans secret", st == 200 and p == {"sounds": []}, (st, p))
    st, p = await call(bot._handle_list_sounds, {"Origin": PUBLIC})
    check("GET /sounds sans session : 401", st == 401)
    st, p = await call(bot._handle_list_sounds, {"Authorization": "Bearer faux", "Origin": PUBLIC})
    check("GET /sounds avec une session inconnue : 401", st == 401)
    st, p = await call(bot._handle_status, {"Origin": PUBLIC})
    check("GET /status sans session ni secret : 401 (rien du salon vocal ne fuit)", st == 401)

    print("\n--- Échange du code Discord (POST /oauth/exchange) ---")
    exchanged = []
    async def fake_token_request(payload):
        exchanged.append(payload)
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    async def fake_identity(session):
        session["user_id"], session["username"], session["avatar_url"] = "77", "bob", "https://cdn.discordapp.com/avatars/77/h.png"
    bot._discord_token_request, bot._refresh_identity_from_discord = fake_token_request, fake_identity
    callback = f"{PUBLIC}/app/callback"
    web_headers = {"Origin": PUBLIC}
    secret_headers = {"X-WaseBoard-Token": S.SHARED_SECRET}

    st, p = await call(bot._handle_oauth_exchange, web_headers, {"code": "c1", "redirect_uri": callback})
    check("page web : code échangé contre une session (sans secret, Origin du site, adresse de retour de la page)",
          st == 200 and p["username"] == "bob" and p["session_token"] in bot.oauth_sessions and len(p["session_token"]) >= 32, (st, p))
    check("…le code est envoyé à Discord avec l'adresse de retour de la page et le secret client (jamais renvoyé à la page)",
          exchanged[-1]["redirect_uri"] == callback and exchanged[-1]["client_secret"] == "clientsecret" and "clientsecret" not in json.dumps(p))
    st, p = await call(bot._handle_oauth_exchange, web_headers, {"code": "c", "redirect_uri": "http://127.0.0.1:48899/"})
    check("page web : l'adresse de boucle locale de l'application Windows lui est refusée", st == 400 and "redirect_uri" in p["error"], (st, p))
    st, p = await call(bot._handle_oauth_exchange, web_headers, {"code": "c", "redirect_uri": "https://evil.example/app/callback"})
    check("page web : une adresse de retour arbitraire est refusée (pas de proxy d'échange)", st == 400)
    st, p = await call(bot._handle_oauth_exchange, {"Origin": "https://evil.example"}, {"code": "c", "redirect_uri": callback})
    check("autre site : 401", st == 401)
    st, p = await call(bot._handle_oauth_exchange, {}, {"code": "c", "redirect_uri": callback})
    check("ni secret ni Origin : 401", st == 401)
    st, p = await call(bot._handle_oauth_exchange, secret_headers, {"code": "c", "redirect_uri": "http://127.0.0.1:48899/"})
    check("application Windows : secret + boucle locale, inchangé", st == 200 and p["session_token"] in bot.oauth_sessions, (st, p))
    st, p = await call(bot._handle_oauth_exchange, secret_headers, {"code": "c", "redirect_uri": "http://localhost:48899/"})
    check("application Windows : localhost aussi", st == 200)
    st, p = await call(bot._handle_oauth_exchange, secret_headers, {"code": "c", "redirect_uri": "https://evil.example/cb"})
    check("application Windows : adresse arbitraire toujours refusée", st == 400)
    st, p = await call(bot._handle_oauth_exchange, secret_headers, {"code": "c", "redirect_uri": callback})
    check("secret + adresse de la page : acceptée", st == 200)
    st, p = await call(bot._handle_oauth_exchange, secret_headers, {"code": "c", "redirect_uri": 12345})
    check("adresse de retour qui n'est pas du texte : 400 (pas de plantage)", st == 400)
    st, p = await call(bot._handle_oauth_exchange, secret_headers, {"code": "c"})
    check("adresse de retour manquante : échange « Activité » (le code part sans redirect_uri ; Discord refuserait un code web)", st == 200)
    st, p = await call(bot._handle_oauth_exchange, web_headers, None)
    check("corps absent : 400", st == 400)
    bot.web_app = None
    st, p = await call(bot._handle_oauth_exchange, web_headers, {"code": "c", "redirect_uri": callback})
    check("page web désactivée : l'Origin ne suffit plus (401)", st == 401)
    bot.web_app = make_app()
    S.OAUTH2_CLIENT_SECRET = ""
    st, p = await call(bot._handle_oauth_exchange, web_headers, {"code": "c", "redirect_uri": callback})
    check("OAuth2 non configuré : 500 explicite", st == 500)
    S.OAUTH2_CLIENT_SECRET = "clientsecret"

    print("\n--- Activité Discord : page dans une iframe, origine, échange du code ---")
    ACT = "https://555.discordsays.com"
    wa = make_app()
    check("origine de l'Activité : https://<identifiant client>.discordsays.com", wa.activity_origin() == ACT and wa.is_activity_origin(ACT) and wa.is_activity_origin(ACT + "/"))
    check("…acceptée comme origine de la page (appels de l'API avec la session)", wa.origin_allowed(ACT))
    check("…mais pas celle d'une autre application, d'un faux domaine ou en http",
          not any(wa.origin_allowed(o) for o in ("https://999.discordsays.com", "https://555.discordsays.com.evil.example", "http://555.discordsays.com", "https://discordsays.com", "https://555.discordsays.com:8443")))
    check("sans identifiant client ou Activité désactivée : aucune origine d'Activité", make_app(client_id="").activity_origin() is None and make_app(activity=False).activity_origin() is None
          and not make_app(activity=False).origin_allowed(ACT))

    client = await serve(wa)
    try:
        r = await client.get("/app/")
        csp_normal = r.headers["Content-Security-Policy"]
        r = await client.get("/app/?frame_id=123&instance_id=abc&platform=desktop")
        csp_frame = r.headers["Content-Security-Policy"]
        check("page chargée par Discord (frame_id) : peut s'afficher dans un cadre, tout le reste de la politique inchangé",
              r.status == 200 and "frame-ancestors" not in csp_frame and csp_frame == csp_normal.replace("; frame-ancestors 'none'", "")
              and "script-src 'self'" in csp_frame and "'unsafe-inline'" not in csp_frame, csp_frame)
        check("…alors que la page ordinaire reste interdite de cadre", "frame-ancestors 'none'" in csp_normal)
        r = await client.get("/?frame_id=123&instance_id=abc")
        check("la racine du domaine, chargée par le proxy de Discord, sert la page", r.status == 200 and "<title>WaseBoard</title>" in await r.text() and "frame-ancestors" not in r.headers["Content-Security-Policy"])
        r = await client.get("/")
        check("la racine sans frame_id : rien (la page d'accueil du site n'est pas celle du serveur)", r.status == 404)
        r = await client.get("/app/app.js?frame_id=1")
        check("un script avec frame_id garde la politique stricte", "frame-ancestors 'none'" in r.headers["Content-Security-Policy"])
        r = await client.get("/app/vendor/discord-embedded-app-sdk.js")
        check("le kit de Discord est servi comme les autres scripts (même origine)", r.status == 200 and r.content_type == "text/javascript" and len(await r.read()) > 50000)
    finally:
        await client.close()
    client = await serve(make_app(activity=False))
    try:
        r = await client.get("/?frame_id=1")
        r2 = await client.get("/app/?frame_id=1")
        check("Activité désactivée : racine introuvable, et la page reste interdite de cadre même avec frame_id",
              r.status == 404 and "frame-ancestors 'none'" in r2.headers["Content-Security-Policy"])
    finally:
        await client.close()

    bot.web_app = make_app()
    exchanged.clear()
    st, p = await call(bot._handle_oauth_exchange, {"Origin": ACT}, {"code": "act1"})
    check("échange du code de l'Activité SANS adresse de retour : session créée, jeton Discord renvoyé pour « authenticate »",
          st == 200 and p["discord_access_token"] == "at" and p["session_token"] in bot.oauth_sessions and p["username"] == "bob", (st, p))
    check("…Discord reçoit la demande sans redirect_uri, avec le secret client (qui ne sort pas)",
          "redirect_uri" not in exchanged[-1] and exchanged[-1]["code"] == "act1" and exchanged[-1]["client_secret"] == "clientsecret")
    st, p = await call(bot._handle_oauth_exchange, {}, {"code": "act2"})
    check("sans en-tête Origin (le proxy de Discord peut le retirer) : accepté aussi", st == 200 and "discord_access_token" in p, (st, p))
    st, p = await call(bot._handle_oauth_exchange, {"Origin": ACT}, {})
    check("sans code : 400", st == 400, (st, p))
    st, p = await call(bot._handle_oauth_exchange, {"Origin": ACT}, {"code": "x", "redirect_uri": "https://evil.example/cb"})
    check("avec une adresse de retour arbitraire : toujours refusée (400)", st == 400, (st, p))
    st, p = await call(bot._handle_oauth_exchange, {}, {"code": "x", "redirect_uri": f"{PUBLIC}/app/callback"})
    check("une adresse de retour sans origine ni secret : 401 (le mode Activité ne rouvre pas les autres)", st == 401, (st, p))
    st, p = await call(bot._handle_oauth_exchange, {"Origin": PUBLIC}, {"code": "x", "redirect_uri": f"{PUBLIC}/app/callback"})
    check("connexion web ordinaire : jamais de jeton Discord dans la réponse", st == 200 and "discord_access_token" not in p, (st, p))
    st, p = await call(bot._handle_oauth_exchange, {"X-WaseBoard-Token": S.SHARED_SECRET}, {"code": "x", "redirect_uri": "http://127.0.0.1:48899/"})
    check("application Windows : idem, jamais de jeton Discord", st == 200 and "discord_access_token" not in p, (st, p))
    session_token = next(iter(reversed(list(bot.oauth_sessions))))
    check("la session de l'Activité s'utilise ensuite depuis l'origine de l'Activité",
          bot._check_auth(Req({"Authorization": f"Bearer {session_token}", "Origin": ACT})))
    bot.web_app = make_app(activity=False)
    st, p = await call(bot._handle_oauth_exchange, {"Origin": ACT}, {"code": "x"})
    check("Activité désactivée : l'échange sans adresse de retour est refusé", st in (400, 401), (st, p))
    bot.web_app = make_app()
    refused_before = set(bot.web_app._refused_origins)
    bot._check_auth(Req({"Authorization": "Bearer inconnu", "Origin": "https://proxy-inattendu.example"}))
    check("une origine inattendue est journalisée une fois (diagnostic d'un proxy qui réécrit l'en-tête)",
          "https://proxy-inattendu.example" in bot.web_app._refused_origins and "https://proxy-inattendu.example" not in refused_before)
    for i in range(40):
        bot.web_app.note_refused_origin(f"https://spam{i}.example")
    check("…sans jamais remplir la mémoire ni le journal", len(bot.web_app._refused_origins) <= 20)

    print("\n--- Activité Discord : commande de lancement (point d'entrée) ---")

    class FakeHttp:
        def __init__(self, existing=(), fail=None):
            self.existing, self.fail, self.created = list(existing), fail, []

        async def get_global_commands(self, app_id):
            if self.fail:
                raise self.fail
            return list(self.existing)

        async def upsert_global_command(self, app_id, payload):
            self.created.append((app_id, payload))

    real_http, real_app_id = bot.http, bot._connection.application_id
    bot._connection.application_id = 777
    try:
        bot.http = FakeHttp()
        await bot._ensure_activity_entry_point()
        (app_id, payload), = bot.http.created
        check("sans commande de lancement : elle est créée (type 4 = point d'entrée, handler 2 = Discord lance l'Activité)",
              app_id == 777 and payload["type"] == 4 and payload["handler"] == 2 and payload["name"] == "launch" and 1 <= len(payload["description"]) <= 100, payload)
        bot.http = FakeHttp(existing=[{"type": 1, "name": "play"}, {"type": 1, "name": "join"}])
        await bot._ensure_activity_entry_point()
        check("avec seulement des commandes slash : créée aussi", len(bot.http.created) == 1)
        bot.http = FakeHttp(existing=[{"type": 1, "name": "play"}, {"type": 4, "name": "lancer"}])
        await bot._ensure_activity_entry_point()
        check("une commande de lancement existe déjà (quel que soit son nom) : rien n'est créé ni écrasé", bot.http.created == [])
        bot.http = FakeHttp(fail=RuntimeError("Activities not enabled"))
        await bot._ensure_activity_entry_point()
        check("échec de Discord (Activités pas activées au portail, réseau) : journalisé, jamais d'exception au démarrage", bot.http.created == [])
        S.WEB_ACTIVITY_ENABLED = False
        bot.http = FakeHttp()
        await bot._ensure_activity_entry_point()
        check("Activité désactivée : aucun appel à Discord", bot.http.created == [])
        S.WEB_ACTIVITY_ENABLED = True
        bot._connection.application_id = None
        bot.http = FakeHttp()
        await bot._ensure_activity_entry_point()
        check("identifiant d'application inconnu : aucun appel", bot.http.created == [])
    finally:
        bot.http, bot._connection.application_id = real_http, real_app_id
        S.WEB_ACTIVITY_ENABLED = True

    print("\n--- Déconnexion depuis la page ---")
    session_token = "to-delete-12345678901234567890123456"
    bot.oauth_sessions[session_token] = {"user_id": "5", "username": "carl", "created_at": 0, "last_used_at": 0}
    st, p = await call(bot._handle_oauth_logout, {"Authorization": f"Bearer {session_token}", "Origin": PUBLIC})
    check("POST /oauth/logout : la session (et ses jetons Discord) est supprimée du serveur", st == 200 and session_token not in bot.oauth_sessions)
    st, p = await call(bot._handle_oauth_logout, {"Authorization": f"Bearer {session_token}", "Origin": PUBLIC})
    check("…une session déjà absente : 401 (la page l'oublie de son côté)", st == 401)

    print("\n--- Limitation de débit ---")
    class RL:
        def __init__(self, method, path): self.method, self.path = method, path
    check("l'échange du code reste dans le seau « oauth »", S.rate_limit_bucket(RL("POST", "/oauth/exchange")) == "oauth")
    check("les fichiers de la page et l'API de lecture ne sont pas freinés", S.rate_limit_bucket(RL("GET", "/app/app.js")) is None and S.rate_limit_bucket(RL("POST", "/play")) is None)

    print("\n--- Configuration ---")
    P = _env.DATA_DIR / "inexistant.json"
    cfg = wb_config.load_config(environ={}, config_path=P)
    check("par défaut : page activée dans la configuration (S.WEB_APP_ENABLED)", S.WEB_APP_ENABLED is True and S.WEB_REDIRECT_URIS == [] and S.WEB_DIR == Path(S.__file__).parent / "web")
    cfg = wb_config.load_config(environ={"WASEBOARD_WEB_APP_ENABLED": "false", "WASEBOARD_WEB_REDIRECT_URIS": '["https://a.example/app/callback"]'}, config_path=P)
    check("variables d'environnement prises en compte", cfg["web_app_enabled"] is False and cfg["web_redirect_uris"] == ["https://a.example/app/callback"], cfg)
    cfg = wb_config.load_config(environ={"WASEBOARD_WEB_ACTIVITY_ENABLED": "false"}, config_path=P)
    check("Activité Discord : activée par défaut (S.WEB_ACTIVITY_ENABLED), désactivable par WASEBOARD_WEB_ACTIVITY_ENABLED",
          S.WEB_ACTIVITY_ENABLED is True and cfg["web_activity_enabled"] is False, cfg)
    try:
        wb_config.validate_limits({"web_activity_enabled": "oui"})
        check("web_activity_enabled doit être un booléen", False)
    except wb_config.ConfigError:
        check("web_activity_enabled doit être un booléen", True)

    def refused(values):
        try:
            wb_config.validate_limits(values)
        except wb_config.ConfigError:
            return True
        return False
    check("valeurs invalides refusées au démarrage", refused({"web_app_enabled": "oui"}) and refused({"web_redirect_uris": "https://x"}) and refused({"web_redirect_uris": ["ftp://x"]})
          and refused({"web_redirect_uris": ["https://x/a b"]}) and refused({"web_redirect_uris": ["https://x/cb#frag"]}) and refused({"web_redirect_uris": [5]}))
    for ok_values in ({"web_app_enabled": False}, {"web_redirect_uris": []}, {"web_redirect_uris": None}, {"web_redirect_uris": ["http://localhost:5005/app/callback"]}):
        wb_config.validate_limits(ok_values)
    check("liste vide, null et adresses valides acceptées", True)
    try:
        wb_config.load_config(environ={"WASEBOARD_WEB_REDIRECT_URIS": '"pas une liste"'}, config_path=P)
        check("liste JSON d'environnement mal formée : refusée", False)
    except wb_config.ConfigError:
        check("liste JSON d'environnement mal formée : refusée", True)

    print("\n--- Fonctionnalité annoncée ---")
    source = Path(S.__file__).read_text(encoding="utf-8")
    check("« web_app » annoncé par /status seulement si la page est activée, complète et si public_url est valide",
          '(["web_app"] if WEB_APP_ENABLED and webapp.origin_of(PUBLIC_URL) and (WEB_DIR / "index.html").exists() else [])' in source)
    check("la page est enregistrée au démarrage du serveur HTTP, si elle se charge", "web_app.add_routes(app.router)" in source and "if web_app.load():" in source)


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
