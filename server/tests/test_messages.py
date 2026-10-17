"""Messages d'erreur bilingues (messages.py + language_middleware de server.py) : choix de la langue (Accept-Language), traduction des
messages fixes et à trous, couverture du catalogue (chaque message d'erreur écrit dans le code a sa traduction) et comportement
réel sur HTTP. Vrai code, vrai serveur HTTP de test (aiohttp). Dossier de données isolé (_env.py)."""
import ast
import asyncio
import json
import re
import sys
from pathlib import Path

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import messages as M  # noqa: E402
import server as S  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


SERVER_DIR = Path(S.__file__).parent

print("--- Choix de la langue (Accept-Language) ---")
check("sans en-tête : français (comportement d'avant)", M.pick_language(None) == "fr" and M.pick_language("") == "fr")
check("fr, fr-FR, FR : français", all(M.pick_language(v) == "fr" for v in ("fr", "fr-FR", "FR", "fr-CA,fr;q=0.9")))
check("en, en-US, EN-gb : anglais", all(M.pick_language(v) == "en" for v in ("en", "en-US", "EN-gb")))
check("l'ordre de préférence (q) est respecté", M.pick_language("fr;q=0.5, en;q=0.9") == "en" and M.pick_language("en;q=0.2, fr;q=0.8") == "fr")
check("langue non gérée : français ; première langue gérée sinon", M.pick_language("de-DE") == "fr" and M.pick_language("de, en;q=0.5") == "en")
check("q=0 : langue refusée", M.pick_language("en;q=0, fr") == "fr" and M.pick_language("en;q=0") == "fr")
check("en-tête illisible : français, jamais d'exception", M.pick_language(";;;,,q=") == "fr" and M.pick_language("en;q=abc") in ("fr", "en"))

print("\n--- Traduction ---")
check("message fixe", M.translate("son introuvable", "en") == "sound not found")
check("message à trou", M.translate("Trop de requêtes : réessayez dans 7 s.", "en") == "Too many requests: try again in 7 s.")
check("plusieurs trous, dans l'ordre", M.translate("Son trop long (12 s, maximum 10 s sur ce serveur).", "en") == "Sound too long (12 s, maximum 10 s on this server).")
check("trou en fin de message (texte libre, ponctuation comprise)", M.translate("Échange avec Discord échoué : HTTP 400: invalid_grant", "en") == "Exchange with Discord failed: HTTP 400: invalid_grant")
check("le message le plus précis l'emporte (« Étape 2 : un objet est attendu. »)", M.translate("Étape 2 : un objet est attendu.", "en") == "Step 2: an object is expected.")
check("…et le message imbriqué d'une étape est traduit lui aussi", M.translate("Étape 3 : « volume » doit être un nombre.", "en") == "Step 3: “volume” must be a number.")
check("accolades littérales du message conservées", M.translate("« effects » doit être un objet, par exemple {\"pitch\": 3, \"echo\": true}.", "en").endswith("{\"pitch\": 3, \"echo\": true}."))
check("français demandé : inchangé", M.translate("son introuvable", "fr") == "son introuvable")
check("message inconnu : inchangé", M.translate("Quelque chose d'imprévu", "en") == "Quelque chose d'imprévu")
check("déjà en anglais (« unauthorized ») : inchangé", M.translate("unauthorized", "en") == "unauthorized")
check("langue non gérée ou valeur non textuelle : inchangé, sans exception", M.translate("son introuvable", "de") == "son introuvable" and M.translate(None, "en") is None)

print("\n--- Cohérence du catalogue ---")
frenches = [fr for fr, _ in M.CATALOG]
check("aucun doublon de texte français", len(frenches) == len(set(frenches)))
bad = [fr for fr, en in M.CATALOG
       if fr.count("{}") != len(set(re.findall(r"\{(\d+)\}", en))) or sorted(set(re.findall(r"\{(\d+)\}", en))) != [str(i) for i in range(fr.count("{}"))]]
check("chaque trou {} du français a son {n} dans l'anglais", not bad, bad)
check("aucune traduction vide ou identique au français", all(en.strip() and en != fr for fr, en in M.CATALOG))


def rendered_messages(path):
    """Les messages d'erreur écrits dans un module : valeur de « error » des réponses JSON, PlayRefused, ValueError, refus de capacité, message d'autorisation."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def render(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = render(node.left), render(node.right)
            return None if left is None or right is None else left + right
        return None

    found = {}

    def add(node):
        text = render(node)
        if text is not None:
            found.setdefault(text, node.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "error" and value is not None:
                    add(value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "PlayRefused" and node.args:
            add(node.args[0])
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "ValueError" and node.exc.args:
            add(node.exc.args[0])
        elif isinstance(node, ast.FunctionDef) and node.name == "_play_capacity_error":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    add(sub.value)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "message" for t in node.targets):
            if isinstance(node.value, ast.IfExp):
                add(node.value.body)
                add(node.value.orelse)
            else:
                add(node.value)
    return found


FRENCH = re.compile(r"[À-ÿ]|\b(le|la|les|un|une|des|du|vous|pour|dans|sur|avec|est|pas|ne|ou|et|requis|introuvable|invalide|impossible|trop|manquant)\b", re.I)
untranslated = []
total = 0
for module in ("server.py", "effects.py"):
    for text, line in rendered_messages(SERVER_DIR / module).items():
        if text == "unauthorized" or not FRENCH.search(text):
            continue
        total += 1
        if not M.is_translated(text):
            untranslated.append(f"{module}:{line} {text!r}")
check(f"chacun des {total} messages d'erreur de server.py et effects.py a sa traduction anglaise", total > 50 and not untranslated, untranslated)
unused = [fr for fr in frenches if fr not in {t for module in ("server.py", "effects.py") for t in rendered_messages(SERVER_DIR / module)}]
check("aucune entrée du catalogue ne reste sans message correspondant dans le code", not unused, unused)


async def main():
    print("\n--- Sur HTTP ---")

    async def not_found(request):
        return web.json_response({"error": "son introuvable"}, status=404)

    async def too_long(request):
        return web.json_response({"error": "Son trop long (12 s, maximum 10 s sur ce serveur).", "retry_after": 3}, status=413)

    async def ok(request):
        return web.json_response({"error": "son introuvable", "status": "ok"})

    async def text_error(request):
        return web.Response(text="son introuvable", status=404)

    async def unknown(request):
        return web.json_response({"error": "Quelque chose d'imprévu"}, status=500)

    app = web.Application(middlewares=[S.language_middleware])
    app.router.add_get("/nf", not_found)
    app.router.add_get("/long", too_long)
    app.router.add_get("/ok", ok)
    app.router.add_get("/text", text_error)
    app.router.add_get("/unknown", unknown)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        async def get(path, language=None):
            headers = {"Accept-Language": language} if language else {}
            response = await client.get(path, headers=headers)
            return response, await response.read()

        response, body = await get("/nf")
        check("sans Accept-Language : message français inchangé", json.loads(body)["error"] == "son introuvable" and response.status == 404)
        response, body = await get("/nf", "fr")
        check("Accept-Language: fr : inchangé", json.loads(body)["error"] == "son introuvable")
        response, body = await get("/nf", "en")
        check("Accept-Language: en : message anglais, même statut, JSON valide",
              json.loads(body)["error"] == "sound not found" and response.status == 404 and response.content_type == "application/json", body)
        check("…longueur annoncée exacte", int(response.headers.get("Content-Length", len(body))) == len(body))
        response, body = await get("/long", "en-US,en;q=0.9")
        data = json.loads(body)
        check("message à trous traduit, autres champs conservés", data["error"] == "Sound too long (12 s, maximum 10 s on this server)." and data["retry_after"] == 3 and response.status == 413, body)
        response, body = await get("/ok", "en")
        check("une réponse réussie n'est jamais modifiée", json.loads(body)["error"] == "son introuvable" and response.status == 200)
        response, body = await get("/text", "en")
        check("une réponse qui n'est pas du JSON n'est pas touchée", body == b"son introuvable")
        response, body = await get("/unknown", "en")
        check("message inconnu du catalogue : conservé tel quel", json.loads(body)["error"] == "Quelque chose d'imprévu" and response.status == 500)
        response, body = await get("/nf", "de-DE")
        check("langue non gérée : français", json.loads(body)["error"] == "son introuvable")
    finally:
        await client.close()

    print("\n--- Installation dans server.py ---")
    source = (SERVER_DIR / "server.py").read_text(encoding="utf-8")
    check("le middleware de langue est le premier (il traduit aussi le refus de débit « Trop de requêtes »)",
          "middlewares=[language_middleware] + ([rate_limit_middleware]" in source)
    limited = web.Application(middlewares=[S.language_middleware, S.rate_limit_middleware])
    check("le refus de débit passe bien par le middleware de langue",
          M.translate(f"Trop de requêtes : réessayez dans 3 s.", "en").startswith("Too many requests") and limited is not None)


asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
