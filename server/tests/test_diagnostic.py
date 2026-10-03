"""Jalon 6 : configuration (fichier + variables d'environnement), contrôles de diagnostic, /health, /diagnostic,
ID OAuth2 déduit du bot, modes --check/--invite-url. Vrai code de wb_config.py / diagnostics.py / server.py,
fausses réponses Discord. Lancé via tests/run_all.sh (dossier de données isolé et jetable, voir _env.py)."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
import wb_config  # noqa: E402
import diagnostics as D  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def by_title(checks, fragment):
    return [c for c in checks if fragment.lower() in c.title.lower()]


def status_of(checks, fragment):
    found = by_title(checks, fragment)
    return found[0].status if found else None


# ---------- faux Discord REST ----------
class FakeResp:
    def __init__(self, status, body): self.status, self._body = status, body
    async def json(self, content_type=None): return self._body
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class FakeSession:
    """routes : suffixe d'URL -> (status, body) ou exception à lever."""
    def __init__(self, routes): self.routes, self.calls = routes, []
    def get(self, url, **kw):
        self.calls.append((url, kw.get("headers", {})))
        for suffix, result in self.routes.items():
            if url.split("?")[0].endswith(suffix):
                if isinstance(result, Exception): raise result
                return FakeResp(*result)
        return FakeResp(404, None)


OK_APP = {"id": "555", "flags": D.FLAG_GUILD_MEMBERS_LIMITED, "redirect_uris": [D.OAUTH_REDIRECT], "bot_require_code_grant": False}


def routes(me=(200, {"id": "555", "username": "WaseBot"}), app=(200, OK_APP), guilds=(200, [{"id": "1"}])):
    return {"/users/@me": me, "/applications/@me": app, "/users/@me/guilds": guilds}


async def main():
    print("--- wb_config : fichier + variables d'environnement ---")
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "config.json"
        check("fichier absent -> config vide, pas d'erreur", wb_config.load_config({}, cfg_path) == {})
        cfg_path.write_text(json.dumps({"bot_token": "from-file", "guild_id": 123, "http_port": 5005,
                                        "shared_secret": "file-secret"}), encoding="utf-8")
        c = wb_config.load_config({}, cfg_path)
        check("config.json seul : inchangé (comportement actuel)", c == {"bot_token": "from-file", "guild_id": 123,
                                                                           "http_port": 5005, "shared_secret": "file-secret"}, c)
        c = wb_config.load_config({"WASEBOARD_BOT_TOKEN": "from-env", "WASEBOARD_HTTP_PORT": "6000",
                                   "WASEBOARD_GUILD_ID": "999", "WASEBOARD_PUBLIC_URL": "https://x.test"}, cfg_path)
        check("l'environnement l'emporte sur le fichier", c["bot_token"] == "from-env" and c["http_port"] == 6000
              and c["guild_id"] == 999 and c["public_url"] == "https://x.test" and c["shared_secret"] == "file-secret", c)
        c = wb_config.load_config({"WASEBOARD_BOT_TOKEN": "   ", "WASEBOARD_GUILD_ID": ""}, cfg_path)
        check("variable vide (ligne vide d'un .env) ignorée : on garde le fichier",
              c["bot_token"] == "from-file" and c["guild_id"] == 123, c)
        c = wb_config.load_config({"WASEBOARD_BOT_TOKEN": "t"}, Path(tmp) / "absent.json")
        check("tout depuis l'environnement, sans config.json", c == {"bot_token": "t"}, c)
        cfg_path.write_text(json.dumps({"guild_id": "456"}), encoding="utf-8")
        check("guild_id texte -> entier", wb_config.load_config({}, cfg_path)["guild_id"] == 456)
        cfg_path.write_text(json.dumps({"guild_id": None}), encoding="utf-8")
        check("guild_id null -> None", wb_config.load_config({}, cfg_path)["guild_id"] is None)
        for label, env in (("port non numérique", {"WASEBOARD_HTTP_PORT": "abc"}), ("guild_id non numérique", {"WASEBOARD_GUILD_ID": "x1"})):
            try:
                wb_config.load_config(env, cfg_path); ok = False; msg = ""
            except wb_config.ConfigError as ex:
                ok = True; msg = str(ex)
            check(f"{label} -> ConfigError lisible", ok and "WASEBOARD" in msg, msg)
        cfg_path.write_text("{ pas du json", encoding="utf-8")
        try:
            wb_config.load_config({}, cfg_path); ok = False
        except wb_config.ConfigError as ex:
            ok = "JSON" in str(ex)
        check("JSON invalide -> ConfigError lisible", ok)
        cfg_path.write_text("[1, 2]", encoding="utf-8")
        try:
            wb_config.load_config({}, cfg_path); ok = False
        except wb_config.ConfigError:
            ok = True
        check("JSON qui n'est pas un objet -> ConfigError", ok)

    out = subprocess.run([sys.executable, "-c", "import wb_config; print(wb_config.DATA_DIR, wb_config.CONFIG_PATH)"],
                         cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env={**os.environ, "WASEBOARD_DATA_DIR": "/tmp/wbdata-x"})
    check("WASEBOARD_DATA_DIR déplace le dossier de données ET config.json", out.stdout.split() == ["/tmp/wbdata-x", "/tmp/wbdata-x/config.json"], out.stdout + out.stderr)
    out = subprocess.run([sys.executable, "-c", "import wb_config; print(wb_config.DATA_DIR == wb_config.BASE_DIR)"],
                         cwd=str(_env.SERVER_DIR), capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "WASEBOARD_DATA_DIR"})
    check("sans variable : données à côté du script, comme avant", out.stdout.strip() == "True", out.stdout + out.stderr)

    print("\n--- check_config ---")
    good = {"bot_token": "t", "shared_secret": "x" * 40, "public_url": "https://wb.exemple.com", "oauth2_client_secret": "s"}
    cs = D.check_config(good)
    check("config complète : aucun avertissement", all(c.status == "ok" for c in cs), [(c.title, c.status) for c in cs])
    check("sans jeton : ❌", status_of(D.check_config({**good, "bot_token": ""}), "jeton") == "fail")
    for secret in ("", "changez-moi", "  Changeme "):
        check(f"secret {secret!r} : ❌ (API ouverte)", status_of(D.check_config({**good, "shared_secret": secret}), "secret partagé") == "fail")
    check("secret de 20 caractères : ⚠️", status_of(D.check_config({**good, "shared_secret": "y" * 20}), "secret partagé") == "warn")
    check("public_url vide : ⚠️", status_of(D.check_config({**good, "public_url": ""}), "public_url") == "warn")
    check("public_url http sur Internet : ⚠️ (secret en clair)", status_of(D.check_config({**good, "public_url": "http://wb.exemple.com"}), "public_url") == "warn")
    for local in ("http://192.168.1.165:5007", "http://127.0.0.1:5005", "http://10.0.0.4", "http://172.20.1.1", "http://mon-nas.local"):
        check(f"public_url locale en http acceptée ({local})", status_of(D.check_config({**good, "public_url": local}), "public_url") == "ok")
    check("172.15.x.x n'est PAS un réseau privé : ⚠️", status_of(D.check_config({**good, "public_url": "http://172.15.0.1"}), "public_url") == "warn")
    check("public_url absurde : ❌", status_of(D.check_config({**good, "public_url": "waseboard.exemple"}), "public_url") == "fail")
    check("sans secret OAuth2 : ❌", status_of(D.check_config({**good, "oauth2_client_secret": ""}), "oauth2") == "fail")

    print("\n--- check_tools / check_data_dir ---")
    ct = D.check_tools()
    check("ffmpeg et ffprobe trouvés", status_of(ct, "ffmpeg") == "ok" and status_of(ct, "ffprobe") == "ok", [(c.title, c.status) for c in ct])
    check("opus et PyNaCl chargés", status_of(ct, "opus") == "ok" and status_of(ct, "pynacl") == "ok", [(c.title, c.status, c.detail) for c in ct])
    with tempfile.TemporaryDirectory() as tmp:
        cd = D.check_data_dir(Path(tmp) / "sous" / "dossier")
        check("dossier créé et inscriptible : ✅", cd[0].status in ("ok", "warn") and (Path(tmp) / "sous" / "dossier").is_dir(), cd)
        check("aucun fichier de test laissé", not list((Path(tmp) / "sous" / "dossier").iterdir()))
        ro = Path(tmp) / "ro"; ro.mkdir(); ro.chmod(0o500)
        cd = D.check_data_dir(ro)
        if os.geteuid() == 0:
            print("SKIP  dossier en lecture seule (root ignore les droits)")
        else:
            check("dossier en lecture seule : ❌", cd[0].status == "fail", cd)
        ro.chmod(0o700)

    print("\n--- check_discord (faux REST) ---")
    sess = FakeSession(routes())
    cs, app_id = await D.check_discord(sess, "tok")
    check("tout est bon : aucun ❌/⚠️", all(c.status == "ok" for c in cs) and app_id == "555", [(c.title, c.status) for c in cs])
    check("le jeton est envoyé en 'Bot <jeton>' avec un User-Agent Discord", all(h.get("Authorization") == "Bot tok" and h.get("User-Agent", "").startswith("DiscordBot") for _, h in sess.calls), sess.calls[:1])
    cs, app_id = await D.check_discord(FakeSession(routes(me=(401, {"message": "401: Unauthorized"}))), "bad")
    check("jeton refusé : ❌ explicite, pas d'id d'application", status_of(cs, "jeton") == "fail" and app_id is None and "invalide" in cs[0].detail, cs)
    cs, app_id = await D.check_discord(FakeSession(routes(me=(500, None))), "t")
    check("réponse 500 : ❌ sans planter", cs[0].status == "fail" and app_id is None)
    cs, app_id = await D.check_discord(FakeSession(routes(me=aiohttp.ClientError("réseau"))), "t")
    check("discord.com injoignable : ❌ sans planter", cs[0].status == "fail" and "injoignable" in cs[0].detail and app_id is None, cs)
    cs, _ = await D.check_discord(FakeSession(routes(app=(200, {**OK_APP, "flags": 0}))), "t")
    check("intent « Server Members » désactivé : ❌ avec le chemin du portail", status_of(cs, "server members") == "fail" and "Privileged Gateway Intents" in by_title(cs, "server members")[0].detail)
    cs, _ = await D.check_discord(FakeSession(routes(app=(200, {**OK_APP, "flags": D.FLAG_GUILD_MEMBERS}))), "t")
    check("intent activé (bot vérifié, 1<<14) : ✅", status_of(cs, "server members") == "ok")
    cs, _ = await D.check_discord(FakeSession(routes(app=(200, {"id": "555"}))), "t")
    check("champ flags absent : ⚠️ (à vérifier à la main)", status_of(cs, "server members") == "warn")
    cs, _ = await D.check_discord(FakeSession(routes(app=(200, {**OK_APP, "redirect_uris": ["https://autre"]}))), "t")
    check("redirect OAuth2 manquant : ❌ avec l'adresse exacte", status_of(cs, "redirection") == "fail" and D.OAUTH_REDIRECT in by_title(cs, "redirection")[0].detail)
    cs, _ = await D.check_discord(FakeSession(routes(app=(200, {k: v for k, v in OK_APP.items() if k != "redirect_uris"}))), "t")
    check("redirect non exposé par l'API : ⚠️ à vérifier à la main", status_of(cs, "redirection") == "warn")
    cs, _ = await D.check_discord(FakeSession(routes(app=(200, {**OK_APP, "bot_require_code_grant": True}))), "t")
    check("« Requires OAuth2 Code Grant » coché : ❌", status_of(cs, "invitation du bot") == "fail")
    cs, _ = await D.check_discord(FakeSession(routes(app=(403, None))), "t")
    check("/applications/@me inaccessible : ⚠️ sans planter", status_of(cs, "réglages de l'application") == "warn")
    cs, _ = await D.check_discord(FakeSession(routes(guilds=(200, []))), "t")
    check("bot sur aucun serveur : ⚠️ qui renvoie vers --invite-url", status_of(cs, "serveurs") == "warn" and "--invite-url" in by_title(cs, "serveurs")[0].detail)
    cs, _ = await D.check_discord(FakeSession(routes()), "")
    check("sans jeton : aucun appel réseau", cs == [])

    print("\n--- URL d'invitation ---")
    url = D.build_invite_url("555")
    check("client_id, permissions et scopes", "client_id=555" in url and "permissions=3146752" in url and "scope=bot%20applications.commands" in url, url)
    check("permissions = Voir le salon + Se connecter + Parler", D.BOT_PERMISSIONS == 1024 + 1048576 + 2097152)

    print("\n--- check_public_url (vrai petit serveur HTTP local) ---")
    async def health_ok(_): return web.json_response({"status": "ok"})
    async def health_ko(_): return web.json_response({"status": "starting"}, status=503)
    runner_ok = web.AppRunner(web.Application()); runner_ok.app.router.add_get("/health", health_ok)
    await runner_ok.setup(); site = web.TCPSite(runner_ok, "127.0.0.1", 0); await site.start()
    port_ok = site._server.sockets[0].getsockname()[1]
    runner_ko = web.AppRunner(web.Application()); runner_ko.app.router.add_get("/health", health_ko)
    await runner_ko.setup(); site2 = web.TCPSite(runner_ko, "127.0.0.1", 0); await site2.start()
    port_ko = site2._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as real:
        check("/health répond 200 : ✅", (await D.check_public_url(real, f"http://127.0.0.1:{port_ok}/"))[0].status == "ok")
        check("/health répond 503 : ⚠️", (await D.check_public_url(real, f"http://127.0.0.1:{port_ko}"))[0].status == "warn")
        r = await D.check_public_url(real, "http://127.0.0.1:1")
        check("port fermé : ⚠️ (et non ❌, NAT possible)", r[0].status == "warn" and "NAT" not in r[0].detail and "box" in r[0].detail, r)
        check("public_url vide : rien", await D.check_public_url(real, "") == [])
    await runner_ok.cleanup(); await runner_ko.cleanup()

    print("\n--- run_checks (assemblage) ---")
    with tempfile.TemporaryDirectory() as tmp:
        cs, app_id = await D.run_checks({**good, "public_url": ""}, Path(tmp), FakeSession(routes()))
        check("assemble config + outils + dossier + Discord", app_id == "555" and len(cs) >= 9, len(cs))
        cs, app_id = await D.run_checks(good, Path(tmp), FakeSession({}), with_network=False)
        check("with_network=False : pas de contrôle Discord", app_id is None and not by_title(cs, "intent"))

    print("\n--- render ---")
    sample = [D.Check("ok", "A", "bien"), D.Check("warn", "B"), D.Check("fail", "C", "mal"), D.Check("info", "D")]
    md = D.render(sample); plain = D.render(sample, markdown=False)
    check("markdown : titres en gras + compteurs", "✅ **A** — bien" in md and "1 ✅ · 1 ⚠️ · 1 ❌" in md, md)
    check("terminal : sans balises markdown", "**" not in plain and "✅ A — bien" in plain, plain)
    long = [D.Check("warn", f"Contrôle {i}", "x" * 150) for i in range(40)]
    check("message Discord tronqué sous 2000 caractères, compteurs conservés", len(D.render(long)) <= 2000 and D.render(long).endswith("0 ✅ · 40 ⚠️ · 0 ❌"), len(D.render(long)))

    print("\n--- check_guild (faux salons Discord) ---")
    class Perm:
        def __init__(self, v=True, c=True, s=True): self.view_channel, self.connect, self.speak = v, c, s
    class Chan:
        def __init__(self, name, perm): self.name, self._p = name, perm
        def permissions_for(self, me): return self._p
    class Guild:
        def __init__(self, me, chans): self.me, self.voice_channels = me, chans
    class Voice:
        def __init__(self, ch): self.channel = ch
    class Member:
        def __init__(self, voice=None): self.voice = voice
    me = object()
    g = Guild(me, [Chan("Général", Perm()), Chan("Musique", Perm())])
    cg = D.check_guild(g, Member())
    check("tous les salons accessibles : ✅ + salon de l'admin non testé (ℹ️)", status_of(cg, "droits du bot") == "ok" and status_of(cg, "votre salon") == "info", [(c.title, c.status) for c in cg])
    g = Guild(me, [Chan("Général", Perm()), Chan("Secret", Perm(c=False)), Chan("Muet", Perm(s=False, v=False))])
    cg = D.check_guild(g, Member(Voice(g.voice_channels[1])))
    d = by_title(cg, "droits du bot")[0]
    check("salons problématiques : ⚠️ listés avec le droit manquant", d.status == "warn" and "#Secret (Se connecter)" in d.detail and "#Muet (Voir le salon, Parler)" in d.detail and "Général" not in d.detail, d.detail)
    check("l'admin est dans un salon que le bot ne peut pas rejoindre : ❌", status_of(cg, "votre salon") == "fail")
    cg = D.check_guild(Guild(me, [Chan(f"salon{i}", Perm(c=False)) for i in range(10)]), Member())
    check("plus de 6 salons en défaut : liste abrégée", "et 4 autre(s)" in by_title(cg, "droits du bot")[0].detail)
    check("serveur sans salon vocal : ℹ️", status_of(D.check_guild(Guild(me, []), Member()), "salons vocaux") == "info")
    check("bot absent du cache membres : ⚠️ sans planter", D.check_guild(Guild(None, []), Member())[0].status == "warn")

    print("\n--- server.py : ID OAuth2 déduit du bot ---")
    bot = S.bot
    old_id = S.OAUTH2_CLIENT_ID
    S.OAUTH2_CLIENT_ID = ""
    bot._connection.application_id = None
    check("ni config ni bot connecté : vide (le handler répondra proprement)", bot.oauth_client_id() == "")
    bot._connection.application_id = 4242
    check("sans config : repli sur l'application du bot", bot.oauth_client_id() == "4242")
    S.OAUTH2_CLIENT_ID = "777"
    check("la valeur de config.json reste prioritaire (installation existante)", bot.oauth_client_id() == "777")
    S.OAUTH2_CLIENT_ID = ""

    class Req:
        def __init__(self, headers=None): self.headers = headers or {}; self.query = {}
    S.SHARED_SECRET = "s" * 40
    resp = await bot._handle_oauth_client_id(Req({"X-WaseBoard-Token": "s" * 40}))
    check("/oauth/client-id renvoie l'id déduit du bot", json.loads(resp.body) == {"client_id": "4242"}, resp.body)
    bot._connection.application_id = None
    resp = await bot._handle_oauth_client_id(Req({"X-WaseBoard-Token": "s" * 40}))
    check("/oauth/client-id : 500 propre si aucun id disponible", resp.status == 500)
    S.OAUTH2_CLIENT_ID = old_id

    print("\n--- server.py : GET /health ---")
    resp = await bot._handle_health(Req())
    check("bot pas prêt : 503 {'status':'starting'} sans authentification", resp.status == 503 and json.loads(resp.body) == {"status": "starting"}, resp.body)
    bot.is_ready = lambda: True
    resp = await bot._handle_health(Req())
    check("bot prêt : 200 {'status':'ok'}, aucun détail", resp.status == 200 and json.loads(resp.body) == {"status": "ok"})
    # vraie application HTTP du serveur : la route est bien déclarée et publique (aucun en-tête d'authentification)
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); free_port = sock.getsockname()[1]
    S.HTTP_HOST, S.HTTP_PORT = "127.0.0.1", free_port
    await bot._start_http_server()
    async with aiohttp.ClientSession() as real:
        async with real.get(f"http://127.0.0.1:{free_port}/health") as r:
            check("GET /health via la vraie application : 200 sans authentification", r.status == 200 and (await r.json()) == {"status": "ok"})
            check("réponse non mise en cache", r.headers.get("Cache-Control") == "no-store")
        async with real.get(f"http://127.0.0.1:{free_port}/sounds") as r:
            check("les autres routes restent protégées (sans en-tête : 401)", r.status == 401, r.status)
    await bot._web_runner.cleanup()

    print("\n--- server.py : commande /diagnostic ---")
    class FakeFollowup:
        def __init__(self): self.sent = []
        async def send(self, content=None, **kw): self.sent.append((content, kw))
    class FakeResponse:
        def __init__(self): self.sent, self.deferred = [], None
        async def send_message(self, content=None, **kw): self.sent.append((content, kw))
        async def defer(self, **kw): self.deferred = kw
    class FakeInteraction:
        def __init__(self, user, guild): self.user, self.guild = user, guild; self.response = FakeResponse(); self.followup = FakeFollowup()
    import discord

    class FakeGuildPerms:
        def __init__(self, admin): self.administrator = admin
    class FakeDiscordMember(discord.Member):
        voice = None  # remplace la propriété de discord.Member

        def __init__(self, admin):  # noqa: pas d'appel au constructeur réel
            self._admin = admin
        @property
        def guild_permissions(self): return FakeGuildPerms(self._admin)

    async def fake_run_checks(config, data_dir, session, with_network=True):
        return [D.Check("ok", "Faux contrôle", "bien")], "555"
    real_run = D.run_checks
    D.run_checks = fake_run_checks
    try:
        guild = Guild(me, [Chan("Général", Perm())])
        inter = FakeInteraction(FakeDiscordMember(False), guild)
        await S.diagnostic.callback(inter)
        check("non-admin : refus poli, éphémère, aucun contrôle lancé", inter.response.sent and inter.response.sent[0][1].get("ephemeral") is True
              and "administrateurs" in inter.response.sent[0][0] and inter.response.deferred is None)
        inter = FakeInteraction(FakeDiscordMember(True), guild)
        await S.diagnostic.callback(inter)
        text, kw = inter.followup.sent[0]
        check("admin : réponse différée puis résultat éphémère", inter.response.deferred == {"ephemeral": True, "thinking": True} and kw.get("ephemeral") is True)
        check("le résultat contient les contrôles généraux, ceux du serveur et le nombre de serveurs", "Faux contrôle" in text and "Droits du bot sur les salons vocaux" in text and "Serveurs connectés" in text, text)
        async def boom(*a, **k): raise RuntimeError("panne")
        D.run_checks = boom
        inter = FakeInteraction(FakeDiscordMember(True), guild)
        await S.diagnostic.callback(inter)
        check("un contrôle qui plante : message d'erreur, pas de réponse Discord manquante", "diagnostic a échoué" in inter.followup.sent[0][0])
    finally:
        D.run_checks = real_run
    check("la commande est masquée aux non-admins (default_permissions) et réservée aux serveurs", S.diagnostic.default_permissions is not None and S.diagnostic.default_permissions.administrator and S.diagnostic.guild_only)

    print("\n--- ligne de commande (sous-processus, REST seul) ---")
    with tempfile.TemporaryDirectory() as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("WASEBOARD_")}
        env["WASEBOARD_DATA_DIR"] = tmp
        r = subprocess.run([sys.executable, "server.py", "--check"], cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env=env)
        check("--check sans aucune config : code 1, dit ce qui manque (jeton, secret)", r.returncode == 1 and "Jeton du bot" in r.stdout and "Secret partagé" in r.stdout, r.stdout + r.stderr)
        check("--check n'écrit pas de balises markdown", "**" not in r.stdout)
        r = subprocess.run([sys.executable, "server.py"], cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env=env)
        check("démarrage sans jeton : code 1 + message clair (pas de traceback)", r.returncode == 1 and "Jeton du bot manquant" in r.stderr and "Traceback" not in r.stderr, r.stderr)
        r = subprocess.run([sys.executable, "server.py", "--invite-url"], cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env=env)
        check("--invite-url sans jeton : code 1", r.returncode == 1 and "bot_token manquant" in r.stderr, r.stderr)
        env["WASEBOARD_BOT_TOKEN"] = "MTIzNDU2Nzg5MDEyMzQ1Njc4.AAAAAA.invalide"
        r = subprocess.run([sys.executable, "server.py", "--check"], cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env=env, timeout=60)
        check("--check avec un faux jeton : ❌ « refusé par Discord » (vrai appel à discord.com)", r.returncode == 1 and "refusé par Discord" in r.stdout, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, "server.py", "--invite-url"], cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env=env, timeout=60)
        check("--invite-url avec un faux jeton : code 1, rien sur stdout", r.returncode == 1 and r.stdout.strip() == "", r.stdout + r.stderr)
        r = subprocess.run([sys.executable, "server.py"], cwd=str(_env.SERVER_DIR), capture_output=True, text=True, env=env, timeout=60)
        check("démarrage avec un faux jeton : message « refusé le jeton », code 1, pas de traceback",
              r.returncode == 1 and "refusé le jeton" in r.stderr and "Traceback" not in r.stderr, r.stderr[-600:])
        check("le dossier de données demandé a bien été utilisé (sounds_data créé dedans)", (Path(tmp) / "sounds_data").is_dir())

    print()
    print(f"{sum(results)}/{len(results)} vérifications OK")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
