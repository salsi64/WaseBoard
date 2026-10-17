"""Messages d'erreur bilingues (messages.py + language_middleware de server.py) : choix de la langue (Accept-Language), traduction des
messages fixes et à trous, couverture du catalogue (chaque message d'erreur écrit dans le code a sa traduction) et comportement
réel sur HTTP. Vrai code, vrai serveur HTTP de test (aiohttp). Dossier de données isolé (_env.py)."""
import ast
import asyncio
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

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


# ---------- Messages du bot dans Discord ----------
print(chr(10) + "--- Discord : langue de la personne ---")
check("langue de Discord : français pour fr, anglais pour le reste (et pour une langue inconnue)",
      M.discord_language("fr") == "fr" and M.discord_language("en-US") == "en" and M.discord_language("en-GB") == "en"
      and M.discord_language("de") == "en" and M.discord_language("ja") == "en" and M.discord_language("zh-CN") == "en")
check("sans langue connue (ancien appel, objet de test) : français, comme avant", M.discord_language(None) == "fr" and M.discord_language("") == "fr")
check("accepte directement un discord.Locale", M.discord_language(S.discord.Locale.french) == "fr" and M.discord_language(S.discord.Locale.american_english) == "en")

MEMBER_FUNCTIONS = {"_handle_join_voice_button", "help_button", "send_link", "invite_other_button", "join", "leave",
                    "play_command", "stop_command", "top_command", "bilan_command"}


def render_all(node):
    """Textes d'une expression : constante, f-string (trous {}), concaténation, ou les deux branches d'un « a if c else b »."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)]
    if isinstance(node, ast.IfExp):
        return render_all(node.body) + render_all(node.orelse)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [a + b for a in render_all(node.left) for b in render_all(node.right)]
    return []


def discord_messages(path):
    """Les textes en français que le bot envoie dans Discord aux membres, et les descriptions de commandes : (texte -> ligne)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = node.value
    found = {}

    def add(node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and len(node.args) == 2:
            node = node.args[1]
        if isinstance(node, ast.Name) and node.id in constants:
            node = constants[node.id]
        for text in render_all(node):
            found.setdefault(text, node.lineno)

    for fn in ast.walk(tree):
        if isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)) and fn.name in MEMBER_FUNCTIONS:
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.args:
                    owner = call.func.value
                    if (call.func.attr == "send_message" and getattr(owner, "attr", "") == "response") or (call.func.attr == "send" and getattr(owner, "attr", "") == "followup"):
                        add(call.args[0])
        if isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for deco in fn.decorator_list:
                if isinstance(deco, ast.Call):
                    callee = deco.func.attr if isinstance(deco.func, ast.Attribute) else ""
                    if callee == "command":
                        for kw in deco.keywords:
                            if kw.arg == "description":
                                add(kw.value)
                    if callee == "describe":
                        for kw in deco.keywords:
                            add(kw.value)
        if isinstance(fn, ast.FunctionDef) and fn.name == "find_sound_for_command":
            for ret in ast.walk(fn):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple) and len(ret.value.elts) == 2 and isinstance(ret.value.elts[0], ast.Constant) and ret.value.elts[0].value is None:
                    add(ret.value.elts[1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name == "CapacityError" and node.args:
                add(node.args[0])
            if name == "Choice":
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        add(kw.value)
    constants_used = {k: v for k, v in found.items()}
    return constants_used


GENERIC = {"⚠️ {}", "❌ {}"}
messages_found = discord_messages(SERVER_DIR / "server.py")
FRENCH_DISCORD = re.compile(r"[À-ÿ]|\b(le|la|les|un|une|des|du|vous|pour|dans|sur|avec|est|pas|ne|ou|et|derniers|bot|son|sons)\b", re.I)
needing = {t: line for t, line in messages_found.items() if FRENCH_DISCORD.search(t) or t in GENERIC}
untranslated_discord = [f"server.py:{line} {t!r}" for t, line in needing.items() if not M.is_translated(t)]
check(f"chacun des {len(needing)} textes que le bot envoie aux membres dans Discord a sa traduction", len(needing) > 30 and not untranslated_discord, untranslated_discord)
unused_discord = [fr for fr, _ in M.DISCORD_CATALOG if fr not in messages_found]
check("aucune entrée du catalogue Discord ne reste sans texte correspondant dans le code", not unused_discord, unused_discord)
check("les motifs génériques (« ⚠️ {} », « ❌ {} ») sont en dernier : un message précis n'est jamais avalé par eux",
      [fr for fr, _ in M.DISCORD_CATALOG][-2:] == ["⚠️ {}", "❌ {}"])
check("« ❌ message précis » est traduit en entier, pas seulement son préfixe",
      M.translate("❌ Impossible de calculer le bilan (voir les journaux du serveur).", "en") == "❌ Couldn't compute the review (see the server logs).")
check("le message d'une capacité saturée est traduit à l'intérieur de « ⚠️ … »",
      M.translate("⚠️ Trop de sons envoyés : réessayez dans 4 s.", "en") == "⚠️ Too many sounds sent: try again in 4 s.")
check("noms de commandes et d'options en anglais : minuscules, tirets, 32 caractères au plus (règle de Discord)",
      all(re.fullmatch(r"[-_a-z0-9]{1,32}", v) for v in list(M.COMMAND_NAMES_EN.values()) + list(M.PARAMETER_NAMES_EN.values())))

print(chr(10) + "--- Discord : menu des commandes ---")


async def translator_checks():
    translator = S.MessagesTranslator()
    ctx = lambda location: SimpleNamespace(location=location, data=None)
    L = S.app_commands.TranslationContextLocation
    text = S.app_commands.locale_str("Déconnecte le bot du salon vocal")
    en = await translator.translate(text, S.discord.Locale.american_english, ctx(L.command_description))
    check("description d'une commande : anglais pour Discord en anglais", en == "Disconnects the bot from the voice channel", en)
    check("…anglais aussi pour une autre langue (mieux que le français)", await translator.translate(text, S.discord.Locale.german, ctx(L.command_description)) == en)
    check("…rien pour le français : le texte par défaut du code s'affiche", await translator.translate(text, S.discord.Locale.french, ctx(L.command_description)) is None)
    check("nom de commande : traduit si on a une traduction, sinon inchangé (None)",
          await translator.translate(S.app_commands.locale_str("panneau"), S.discord.Locale.american_english, ctx(L.command_name)) == "panel"
          and await translator.translate(S.app_commands.locale_str("join"), S.discord.Locale.american_english, ctx(L.command_name)) is None)
    check("nom d'option : « son » -> « sound »", await translator.translate(S.app_commands.locale_str("son"), S.discord.Locale.american_english, ctx(L.parameter_name)) == "sound")
    check("choix : « 7 derniers jours » -> « Last 7 days »", await translator.translate(S.app_commands.locale_str("7 derniers jours"), S.discord.Locale.british_english, ctx(L.choice_name)) == "Last 7 days")
    check("texte inconnu : None (jamais d'exception)", await translator.translate(S.app_commands.locale_str("quelque chose"), S.discord.Locale.american_english, ctx(L.command_description)) is None)

    await S.bot.tree.set_translator(translator)
    problems = []
    seen_commands = 0
    for command in S.bot.tree.get_commands():
        payload = await command.get_translated_payload(S.bot.tree, translator)
        seen_commands += 1
        loc_names = payload.get("name_localizations") or {}
        loc_desc = payload.get("description_localizations") or {}
        if "fr" in loc_names or "fr" in loc_desc:
            problems.append(f"{command.name}: localisation française inutile")
        if loc_desc.get("en-US") in (None, payload["description"]):
            problems.append(f"{command.name}: description anglaise absente")
        for value in list(loc_names.values()):
            if not re.fullmatch(r"[-_a-z0-9]{1,32}", value):
                problems.append(f"{command.name}: nom localisé invalide {value!r}")
        for value in loc_desc.values():
            if not 1 <= len(value) <= 100:
                problems.append(f"{command.name}: description localisée de {len(value)} caractères")
        for option in payload.get("options", []):
            for value in (option.get("name_localizations") or {}).values():
                if not re.fullmatch(r"[-_a-z0-9]{1,32}", value):
                    problems.append(f"{command.name}.{option['name']}: nom d'option localisé invalide {value!r}")
            for value in (option.get("description_localizations") or {}).values():
                if not 1 <= len(value) <= 100:
                    problems.append(f"{command.name}.{option['name']}: description d'option localisée de {len(value)} caractères")
            for choice in option.get("choices", []):
                for value in (choice.get("name_localizations") or {}).values():
                    if not 1 <= len(value) <= 100:
                        problems.append(f"{command.name}: choix localisé de {len(value)} caractères")
    check(f"les {seen_commands} commandes, une fois traduites, respectent les limites de Discord (noms, descriptions, choix)", seen_commands >= 12 and not problems, problems)
    play = next(c for c in S.bot.tree.get_commands() if c.name == "play")
    payload = await play.get_translated_payload(S.bot.tree, translator)
    check("/play en anglais : description et option « sound » localisées",
          payload["description_localizations"]["en-US"] == "Plays a sound in your voice channel, without opening the app"
          and payload["options"][0]["name_localizations"]["en-US"] == "sound"
          and payload["options"][0]["description_localizations"]["en-US"] == "The sound to play (type a few letters to search)")
    top = next(c for c in S.bot.tree.get_commands() if c.name == "top")
    payload = await top.get_translated_payload(S.bot.tree, translator)
    choices = payload["options"][0]["choices"]
    check("/top : les quatre périodes ont leur nom anglais", [c["name_localizations"]["en-US"] for c in choices] == ["Last 7 days", "Last 30 days", "Last 90 days", "Last 12 months"])


async def main():
    await translator_checks()
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
