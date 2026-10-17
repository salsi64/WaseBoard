"""Catégories personnelles : l'application Windows en envoie une copie (PUT /me/categories) que la version web et l'Activité relisent
(GET /me/categories). La copie est propre à chaque personne, remplacée en entier à chaque envoi, bornée, et effacée quand il n'y a plus
rien à garder. Vrai serveur HTTP de server.py, faux objets Discord, dossier de données isolé (_env.py)."""
import asyncio
import json
import os
import sys
import time

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import aiohttp  # noqa: E402
import messages as M  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


PUBLIC = "https://waseboard.test"
SECRET = "s" * 40
S.SHARED_SECRET = SECRET
S.PUBLIC_URL = PUBLIC
S.HTTP_HOST, S.HTTP_PORT = "127.0.0.1", 0
bot = S.bot

TOKENS = {}
for uid in (20, 21):
    TOKENS[uid] = f"cat-token-{uid}-" + "x" * 24
    bot.oauth_sessions[TOKENS[uid]] = {"user_id": str(uid), "username": f"user{uid}", "avatar_url": None, "discord_access_token": "x",
                                       "discord_refresh_token": "y", "discord_token_expires_at": time.time() + 10 ** 6, "created_at": 0, "last_used_at": 0}

# ---------- Validation (sans réseau) ----------
print("--- Validation du corps ---")
clean, err = S.sanitize_personal_categories({"categories": {"Mèmes": ["a", "b", "a"], "  Jeux ": ["c"]}, "order": ["Jeux", "42", "Mèmes", "Jeux"]})
check("noms nettoyés, doublons retirés (ordre gardé)", err is None and clean == {"categories": {"Mèmes": ["a", "b"], "Jeux": ["c"]}, "order": ["Jeux", "42", "Mèmes"]}, (clean, err))
clean, err = S.sanitize_personal_categories({"categories": {"A": ["x"], " A ": ["y", "x"]}})
check("deux noms identiques après nettoyage : fusionnés", err is None and clean["categories"] == {"A": ["x", "y"]}, (clean, err))
clean, err = S.sanitize_personal_categories({})
check("corps vide : rien à garder, sans erreur", err is None and clean == {"categories": {}, "order": []}, (clean, err))
for label, body in (
    ("corps qui n'est pas un objet", []),
    ("categories qui n'est pas un objet", {"categories": []}),
    ("order qui n'est pas une liste", {"order": {}}),
    ("nom vide", {"categories": {"   ": ["a"]}}),
    ("nom trop long", {"categories": {"x" * (S.USER_CATEGORY_NAME_MAX + 1): []}}),
    ("liste de sons qui n'en est pas une", {"categories": {"A": "abc"}}),
    ("identifiant qui n'est pas du texte", {"categories": {"A": [1]}}),
    ("identifiant vide", {"categories": {"A": [""]}}),
    ("identifiant trop long", {"categories": {"A": ["x" * (S.USER_CATEGORY_ID_MAX + 1)]}}),
    ("clé d'ordre invalide", {"order": [3]}),
    ("trop de catégories", {"categories": {f"c{i}": [] for i in range(S.USER_CATEGORIES_MAX + 1)}}),
    ("trop de sons dans une catégorie", {"categories": {"A": [f"s{i}" for i in range(S.USER_CATEGORY_SOUNDS_MAX + 1)]}}),
    ("trop d'entrées dans l'ordre", {"order": [f"k{i}" for i in range(S.USER_CATEGORIES_ORDER_MAX + 1)]}),
):
    clean, err = S.sanitize_personal_categories(body)
    check(f"refusé : {label}", clean is None and err == ("limit" if label.startswith("trop") else "format"), (clean, err))
limit_ok, err = S.sanitize_personal_categories({"categories": {f"c{i}": [] for i in range(S.USER_CATEGORIES_MAX)}, "order": [f"k{i}" for i in range(S.USER_CATEGORIES_ORDER_MAX)]})
check("pile aux limites : accepté", err is None and len(limit_ok["categories"]) == S.USER_CATEGORIES_MAX, err)
check("les messages d'erreur sont traduits en anglais", all(M.is_translated(message) for message in ("catégories invalides", "trop de catégories ou de sons dans une catégorie")))
check("la fonction est annoncée par /status (le client masque la copie sur un serveur plus ancien)", "personal_categories" in S.SERVER_FEATURES, S.SERVER_FEATURES)


def headers(uid=None, origin=PUBLIC, secret=False):
    result = {}
    if uid:
        result["Authorization"] = f"Bearer {TOKENS[uid]}"
    if origin:
        result["Origin"] = origin
    if secret:
        result["X-WaseBoard-Token"] = SECRET
    return result


async def main():
    await bot._start_http_server()
    base = f"http://127.0.0.1:{bot._web_runner.addresses[0][1]}"
    async with aiohttp.ClientSession() as http:
        async def call(method, path, uid=None, origin=PUBLIC, secret=False, **kwargs):
            async with http.request(method, base + path, headers=headers(uid, origin, secret), **kwargs) as response:
                raw = await response.read()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = raw
                return response.status, body, response.headers

        print("\n--- Accès ---")
        st, _, _ = await call("GET", "/me/categories")
        check("sans session : 401", st == 401, st)
        st, _, _ = await call("PUT", "/me/categories", json={"categories": {"A": []}})
        check("envoi sans session : 401", st == 401, st)
        st, _, _ = await call("GET", "/me/categories", uid=20, origin="https://evil.example")
        check("avec une session mais depuis un autre site : 401", st == 401, st)
        st, _, _ = await call("GET", "/me/categories", uid=20, origin=None)
        check("avec la session seule (sans Origin : lecture de même origine) : 200", st == 200, st)

        print("\n--- Lecture et envoi ---")
        st, body, resp = await call("GET", "/me/categories", uid=20)
        check("rien d'envoyé : listes vides, jamais d'erreur", st == 200 and body["categories"] == {} and body["order"] == [] and body["updated_at"] is None, (st, body))
        check("…réponse jamais mise en cache (donnée personnelle)", "no-store" in resp.get("Cache-Control", ""), dict(resp))
        mine = {"categories": {"Mèmes": ["s1", "s2"], "Jeux": ["s3"]}, "order": ["Mèmes", "3101", "Jeux"]}
        before = time.time()
        st, body, _ = await call("PUT", "/me/categories", uid=20, json=mine)
        check("envoi : 200 et nombre de catégories", st == 200 and body == {"ok": True, "count": 2}, (st, body))
        st, body, _ = await call("GET", "/me/categories", uid=20)
        check("relecture : les catégories et l'ordre sont ceux envoyés", st == 200 and body["categories"] == mine["categories"] and body["order"] == mine["order"], body)
        check("…avec la date de l'envoi", isinstance(body["updated_at"], float) and body["updated_at"] >= before - 1, body)
        st, body, _ = await call("GET", "/me/categories", uid=21)
        check("une autre personne ne voit rien de tout cela", st == 200 and body["categories"] == {} and body["order"] == [], body)
        st, _, _ = await call("PUT", "/me/categories", uid=21, json={"categories": {"Autre": ["z"]}, "order": ["Autre"]})
        st, body, _ = await call("GET", "/me/categories", uid=20)
        check("…et son propre envoi ne touche pas aux miennes", body["categories"] == mine["categories"], body)
        st, body, _ = await call("GET", "/me/categories", uid=20, origin=None, secret=True)
        check("avec le secret partagé ET la session (application Windows) : mêmes données", st == 200 and body["categories"] == mine["categories"], (st, body))
        st, body, _ = await call("GET", "/me/categories", secret=True)
        check("le secret seul ne suffit pas : il faut une session pour savoir de qui il s'agit", st == 401, (st, body))

        print("\n--- Remplacement ---")
        st, body, _ = await call("PUT", "/me/categories", uid=20, json={"categories": {"Jeux": ["s3", "s4"]}, "order": ["Jeux"]})
        st, body, _ = await call("GET", "/me/categories", uid=20)
        check("chaque envoi remplace le précédent en entier (Mèmes a disparu)", body["categories"] == {"Jeux": ["s3", "s4"]} and body["order"] == ["Jeux"], body)
        st, body, _ = await call("PUT", "/me/categories", uid=20, json={"categories": {}, "order": ["3101"]})
        st, body, _ = await call("GET", "/me/categories", uid=20)
        check("sans catégorie mais avec un ordre de serveurs : l'ordre est gardé", body["categories"] == {} and body["order"] == ["3101"], body)
        st, body, _ = await call("PUT", "/me/categories", uid=20, json={"categories": {}, "order": []})
        stored = json.loads(S.USER_CATEGORIES_PATH.read_text(encoding="utf-8"))
        check("plus rien à garder : l'entrée de la personne est supprimée du fichier", "20" not in stored and "21" in stored, stored)
        if os.name != "nt":
            mode = S.USER_CATEGORIES_PATH.stat().st_mode & 0o777
            check("le fichier n'est lisible que par le serveur (0600)", mode == 0o600, oct(mode))

        print("\n--- Envois refusés ---")
        await call("PUT", "/me/categories", uid=20, json=mine)
        st, body, _ = await call("PUT", "/me/categories", uid=20, data=b"pas du json")
        check("corps qui n'est pas du JSON : 400", st == 400, (st, body))
        for label, payload, message in (("nom vide", {"categories": {" ": []}}, "catégories invalides"),
                                        ("identifiant invalide", {"categories": {"A": [7]}}, "catégories invalides"),
                                        ("trop de catégories", {"categories": {f"c{i}": [] for i in range(S.USER_CATEGORIES_MAX + 1)}},
                                         "trop de catégories ou de sons dans une catégorie")):
            st, body, _ = await call("PUT", "/me/categories", uid=20, json=payload)
            check(f"{label} : 400 avec un message", st == 400 and body.get("error") == message, (st, body))
        st, body, _ = await call("GET", "/me/categories", uid=20)
        check("…et un envoi refusé ne change rien à ce qui était gardé", body["categories"] == mine["categories"], body)

        print("\n--- Anglais ---")
        async with http.request("PUT", base + "/me/categories", headers={**headers(20), "Accept-Language": "en"}, json={"categories": {"A": [3]}}) as response:
            english = await response.json()
        check("message d'erreur dans la langue du client", english == {"error": "invalid categories"}, english)

    await bot._web_runner.cleanup()


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
