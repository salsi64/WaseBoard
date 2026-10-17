"""Préférences personnelles (favoris, couleurs, catégories perso, ordre) synchronisées entre l'application Windows, la page web et l'Activité :
fusion à trois voies (preferences.py) et routes /me/preferences, /me/preferences/sync et (3.5.0) /me/categories. Vrai serveur HTTP de server.py,
faux objets Discord, dossier de données isolé (_env.py)."""
import asyncio
import copy
import json
import os
import random
import sys
import time

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import aiohttp  # noqa: E402
import messages as M  # noqa: E402
import preferences as P  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def doc(favorites=(), colors=None, categories=None, order=()):
    return {"favorites": list(favorites), "colors": dict(colors or {}), "categories": {k: list(v) for k, v in (categories or {}).items()}, "order": list(order)}


def as_sets(d):
    return {"favorites": set(d["favorites"]), "colors": d["colors"], "categories": {k: set(v) for k, v in d["categories"].items()}, "order": d["order"]}


# ---------- Nettoyage d'un document ----------
print("--- Nettoyage d'un document reçu ---")
clean, err = P.normalize({"favorites": ["a", "b", "a"], "colors": {"a": "#aabbcc"}, "categories": {"Mèmes": ["x", "y", "x"], "  Jeux ": ["z"]}, "order": ["Jeux", "42", "Jeux"]})
check("doublons retirés, noms rognés, couleurs en majuscules", err is None and clean == doc(["a", "b"], {"a": "#AABBCC"}, {"Mèmes": ["x", "y"], "Jeux": ["z"]}, ["Jeux", "42"]), (clean, err))
clean, err = P.normalize({"categories": {"A": ["x"], " A ": ["y", "x"]}})
check("deux noms identiques après nettoyage : fusionnés", err is None and clean["categories"] == {"A": ["x", "y"]}, (clean, err))
check("absent ou champs manquants : vide, sans erreur", P.normalize(None) == (P.empty_doc(), None) and P.normalize({}) == (P.empty_doc(), None))
for label, body, expected in (
    ("corps qui n'est pas un objet", [], "format"),
    ("favoris qui n'est pas une liste", {"favorites": {}}, "format"),
    ("couleurs qui n'est pas un objet", {"colors": []}, "format"),
    ("couleur invalide", {"colors": {"a": "rouge"}}, "format"),
    ("couleur sur 3 chiffres", {"colors": {"a": "#abc"}}, "format"),
    ("identifiant de favori invalide", {"favorites": [1]}, "format"),
    ("identifiant vide", {"favorites": [""]}, "format"),
    ("identifiant trop long", {"favorites": ["x" * (P.ID_MAX + 1)]}, "format"),
    ("nom de catégorie vide", {"categories": {"  ": []}}, "format"),
    ("nom de catégorie trop long", {"categories": {"x" * (P.CATEGORY_NAME_MAX + 1): []}}, "format"),
    ("contenu de catégorie qui n'est pas une liste", {"categories": {"A": "abc"}}, "format"),
    ("clé d'ordre invalide", {"order": [3]}, "format"),
    ("trop de favoris", {"favorites": [f"s{i}" for i in range(P.FAVORITES_MAX + 1)]}, "limit"),
    ("trop de couleurs", {"colors": {f"s{i}": "#000000" for i in range(P.COLORS_MAX + 1)}}, "limit"),
    ("trop de catégories", {"categories": {f"c{i}": [] for i in range(P.CATEGORIES_MAX + 1)}}, "limit"),
    ("trop de sons dans une catégorie", {"categories": {"A": [f"s{i}" for i in range(P.CATEGORY_SOUNDS_MAX + 1)]}}, "limit"),
    ("trop d'entrées dans l'ordre", {"order": [f"k{i}" for i in range(P.ORDER_MAX + 1)]}, "limit"),
):
    clean, err = P.normalize(body)
    check(f"refusé : {label}", clean is None and err == expected, (clean, err))
big, err = P.normalize({"favorites": [f"s{i}" for i in range(P.FAVORITES_MAX)], "categories": {f"c{i}": [] for i in range(P.CATEGORIES_MAX)}, "order": [f"k{i}" for i in range(P.ORDER_MAX)]})
check("pile aux limites : accepté", err is None and len(big["favorites"]) == P.FAVORITES_MAX, err)

# ---------- Fusion à trois voies ----------
print("\n--- Favoris ---")
check("deux appareils ajoutent des favoris différents : les deux sont gardés",
      as_sets(P.merge(doc(["a"]), doc(["a", "b"]), doc(["a", "c"])))["favorites"] == {"a", "b", "c"})
check("première synchronisation (sans état de départ) : réunion, rien n'est retiré",
      as_sets(P.merge(None, doc(["a"]), doc(["b"])))["favorites"] == {"a", "b"})
check("je retire un favori : il disparaît, les ajouts des autres restent",
      as_sets(P.merge(doc(["a", "b"]), doc(["a"]), doc(["a", "b", "c"])))["favorites"] == {"a", "c"})
check("un autre appareil l'a retiré et je n'y ai pas touché : il ne revient pas",
      as_sets(P.merge(doc(["a", "b"]), doc(["a", "b"]), doc(["a"])))["favorites"] == {"a"})
check("l'ordre du serveur est gardé, mes ajouts viennent après", P.merge(doc(["a"]), doc(["a", "z"]), doc(["a", "c", "b"]))["favorites"] == ["a", "c", "b", "z"])

print("\n--- Couleurs ---")
check("une couleur que je change est prise, les autres couleurs du serveur restent",
      P.merge(doc(colors={"a": "#111111"}), doc(colors={"a": "#222222"}), doc(colors={"a": "#111111", "b": "#333333"}))["colors"] == {"a": "#222222", "b": "#333333"})
check("couleur inchangée de mon côté : celle du serveur (changée ailleurs) est gardée",
      P.merge(doc(colors={"a": "#111111"}), doc(colors={"a": "#111111"}), doc(colors={"a": "#444444"}))["colors"] == {"a": "#444444"})
check("changée des deux côtés : celle de l'appareil qui synchronise",
      P.merge(doc(colors={"a": "#111111"}), doc(colors={"a": "#222222"}), doc(colors={"a": "#444444"}))["colors"] == {"a": "#222222"})
check("je retire une couleur : elle disparaît", P.merge(doc(colors={"a": "#111111"}), doc(), doc(colors={"a": "#111111", "b": "#222222"}))["colors"] == {"b": "#222222"})
check("une couleur ajoutée ailleurs n'est pas prise pour une suppression de ma part", P.merge(doc(), doc(), doc(colors={"a": "#111111"}))["colors"] == {"a": "#111111"})

print("\n--- Catégories ---")
merged = P.merge(doc(categories={"A": ["1"]}), doc(categories={"A": ["1", "2"]}), doc(categories={"A": ["1", "3"]}))
check("même catégorie modifiée des deux côtés : contenus fusionnés", as_sets(merged)["categories"] == {"A": {"1", "2", "3"}}, merged)
check("je crée une catégorie : elle s'ajoute à celles du serveur", set(P.merge(doc(), doc(categories={"Neuve": ["1"]}), doc(categories={"Autre": ["2"]}))["categories"]) == {"Neuve", "Autre"})
check("créée des deux côtés avec le même nom : réunion", as_sets(P.merge(doc(), doc(categories={"A": ["1"]}), doc(categories={"A": ["2"]})))["categories"] == {"A": {"1", "2"}})
check("je supprime une catégorie : elle disparaît", "A" not in P.merge(doc(categories={"A": ["1"]}), doc(), doc(categories={"A": ["1"]}))["categories"])
check("supprimée ailleurs, intacte chez moi : elle ne revient pas", "A" not in P.merge(doc(categories={"A": ["1"]}), doc(categories={"A": ["1"]}), doc())["categories"])
renamed = P.merge(doc(categories={"Vieux": ["1", "2"]}), doc(categories={"Neuf": ["1", "2"]}), doc(categories={"Vieux": ["1", "2", "3"]}))
check("renommer = supprimer + créer : le contenu suit, l'ancien nom disparaît", set(renamed["categories"]) == {"Neuf"} and set(renamed["categories"]["Neuf"]) == {"1", "2"}, renamed)
check("catégorie vide : gardée (on peut la remplir ensuite)", P.merge(doc(), doc(categories={"Vide": []}), doc())["categories"] == {"Vide": []})
check("sans état de départ : jamais de suppression", as_sets(P.merge(None, doc(categories={"A": ["1"]}), doc(categories={"B": ["2"]})))["categories"] == {"A": {"1"}, "B": {"2"}})

print("\n--- Ordre d'affichage ---")
check("l'appareil qui réordonne impose son ordre", P.merge(doc(categories={"A": [], "B": []}, order=["A", "B"]), doc(categories={"A": [], "B": []}, order=["B", "A"]),
                                                           doc(categories={"A": [], "B": []}, order=["A", "B"]))["order"] == ["B", "A"])
check("ordre inchangé chez moi : celui du serveur", P.merge(doc(order=["A", "B"]), doc(order=["A", "B"]), doc(order=["B", "A"]))["order"] == ["B", "A"])
check("les identifiants de serveurs restent dans l'ordre, même inconnus d'ici", P.merge(doc(), doc(categories={"A": []}, order=["3101", "A", "3102"]), doc())["order"] == ["3101", "A", "3102"])
check("une catégorie supprimée sort de l'ordre, une nouvelle s'ajoute à la fin",
      P.merge(doc(categories={"A": [], "B": []}, order=["A", "3101", "B"]), doc(categories={"B": [], "C": []}, order=["A", "3101", "B"]),
              doc(categories={"A": [], "B": []}, order=["A", "3101", "B"]))["order"] == ["3101", "B", "C"])

print("\n--- Propriétés de la fusion ---")
a = doc(["a", "b"], {"a": "#111111"}, {"A": ["1", "2"], "B": []}, ["B", "3101", "A"])
b = doc(["b", "c"], {"a": "#222222", "c": "#333333"}, {"A": ["2", "3"], "C": ["9"]}, ["A", "C"])
once = P.merge(a, b, a)
check("renvoyer deux fois la même chose ne change rien (idempotence)", P.merge(a, b, once) == once)
check("sans modification de ma part, on récupère exactement l'état du serveur", P.merge(a, a, b) == b)
check("le résultat est un document valide", P.normalize(once) == (once, None))
over = P.merge(doc(), doc(favorites=[f"m{i}" for i in range(P.FAVORITES_MAX)]), doc(favorites=[f"s{i}" for i in range(P.FAVORITES_MAX)]))
check("la réunion de deux documents permis est ramenée aux limites", len(over["favorites"]) == P.FAVORITES_MAX)


class Replica:
    """Un appareil : son état local et le dernier état du serveur qu'il a reçu."""
    def __init__(self):
        self.local, self.base = P.empty_doc(), None


def random_edit(rng, replica):
    d, kind = replica.local, rng.choice(["fav", "fav", "color", "create", "delete", "add", "remove", "rename", "order"])
    sounds, names = [f"s{i}" for i in range(6)], list(d["categories"])
    if kind == "fav":
        s = rng.choice(sounds)
        d["favorites"] = [x for x in d["favorites"] if x != s] if s in d["favorites"] else d["favorites"] + [s]
    elif kind == "color":
        s = rng.choice(sounds)
        if s in d["colors"] and rng.random() < 0.4:
            del d["colors"][s]
        else:
            d["colors"][s] = rng.choice(["#111111", "#222222", "#333333"])
    elif kind == "create":
        d["categories"].setdefault(rng.choice(["A", "B", "C", "D"]), [])
    elif kind == "delete" and names:
        del d["categories"][rng.choice(names)]
    elif kind == "add" and names:
        ids = d["categories"][rng.choice(names)]
        s = rng.choice(sounds)
        if s not in ids:
            ids.append(s)
    elif kind == "remove" and names:
        ids = d["categories"][rng.choice(names)]
        if ids:
            ids.remove(rng.choice(ids))
    elif kind == "rename" and names:
        old = rng.choice(names)
        new = rng.choice(["A", "B", "C", "D"])
        if new not in d["categories"]:
            d["categories"][new] = d["categories"].pop(old)
    elif kind == "order":
        keys = list(d["categories"]) + ["3101", "3102"]
        rng.shuffle(keys)
        d["order"] = keys


def sync(server, replica):
    merged = P.merge(replica.base, replica.local, server["doc"])
    server["doc"] = merged
    replica.local, replica.base = copy.deepcopy(merged), copy.deepcopy(merged)


converged = 0
valid = True
for seed in range(300):
    rng = random.Random(seed)
    server, devices = {"doc": P.empty_doc()}, [Replica(), Replica(), Replica()]
    for _ in range(rng.randint(10, 40)):
        device = rng.choice(devices)
        if rng.random() < 0.35:
            sync(server, device)
        else:
            random_edit(rng, device)
        valid = valid and all(P.normalize(server["doc"]) == (server["doc"], None) for _ in (0,))
    for device in devices + devices:      # deux tours : chacun reçoit ce que les autres ont envoyé
        sync(server, device)
    if all(device.local == server["doc"] for device in devices):
        converged += 1
check("300 scénarios aléatoires à trois appareils : tous finissent avec exactement le même état que le serveur", converged == 300, f"{converged}/300")
check("…et le serveur garde toujours un document valide", valid)

# ---------- Routes ----------
PUBLIC = "https://waseboard.test"
SECRET = "s" * 40
S.SHARED_SECRET = SECRET
S.PUBLIC_URL = PUBLIC
S.HTTP_HOST, S.HTTP_PORT = "127.0.0.1", 0
bot = S.bot

TOKENS = {}
for uid in (20, 21, 22):
    TOKENS[uid] = f"prefs-token-{uid}-" + "x" * 24
    bot.oauth_sessions[TOKENS[uid]] = {"user_id": str(uid), "username": f"user{uid}", "avatar_url": None, "discord_access_token": "x",
                                       "discord_refresh_token": "y", "discord_token_expires_at": time.time() + 10 ** 6, "created_at": 0, "last_used_at": 0}

# L'ancien fichier de la 3.5.0 (catégories seules) : repris à la première écriture
S.USER_CATEGORIES_PATH.write_text(json.dumps({"22": {"categories": {"Ancienne": ["o1", "o2"]}, "order": ["Ancienne", "3101"], "updated_at": 1234.5}}), encoding="utf-8")


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
    base_url = f"http://127.0.0.1:{bot._web_runner.addresses[0][1]}"
    async with aiohttp.ClientSession() as http:
        async def call(method, path, uid=None, origin=PUBLIC, secret=False, language=None, **kwargs):
            h = headers(uid, origin, secret)
            if language:
                h["Accept-Language"] = language
            async with http.request(method, base_url + path, headers=h, **kwargs) as response:
                raw = await response.read()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = raw
                return response.status, body, response.headers

        print("\n--- Accès ---")
        for method, path in (("GET", "/me/preferences"), ("POST", "/me/preferences/sync"), ("GET", "/me/categories"), ("PUT", "/me/categories")):
            st, _, _ = await call(method, path, json={})
            check(f"{method} {path} sans session : 401", st == 401, st)
        st, _, _ = await call("GET", "/me/preferences", uid=20, origin="https://evil.example")
        check("avec une session mais depuis un autre site : 401", st == 401, st)
        st, _, _ = await call("GET", "/me/preferences", uid=20, origin=None)
        check("avec la session seule (sans Origin) : 200", st == 200, st)
        st, _, _ = await call("GET", "/me/preferences", secret=True)
        check("le secret seul ne suffit pas : il faut une session", st == 401, st)
        check("annoncé par /status : preferences_sync (et personal_categories pour la 3.5.0)", {"preferences_sync", "personal_categories"} <= set(S.SERVER_FEATURES), S.SERVER_FEATURES)

        print("\n--- Synchronisation entre deux appareils ---")
        st, body, resp = await call("GET", "/me/preferences", uid=20)
        check("rien d'envoyé : document vide, révision 0, jamais mis en cache", st == 200 and body == {"rev": 0, "doc": P.empty_doc()} and "no-store" in resp.get("Cache-Control", ""), (st, body))
        pc = doc(["s1", "s2"], {"s1": "#aabbcc"}, {"Mèmes": ["s1", "s2"], "Jeux": ["s3"]}, ["Mèmes", "3101", "Jeux"])
        st, body, _ = await call("POST", "/me/preferences/sync", uid=20, json={"base": None, "doc": pc})
        pc_base = body["doc"]
        check("1er appareil (PC) : accepté, couleur normalisée, révision 1", st == 200 and body["rev"] == 1 and pc_base["colors"] == {"s1": "#AABBCC"} and pc_base["categories"] == pc["categories"], (st, body))
        phone = doc(["s9"], {"s9": "#112233"}, {"Mèmes": ["s7"]}, [])
        st, body, _ = await call("POST", "/me/preferences/sync", uid=20, json={"base": None, "doc": phone})
        phone_base = body["doc"]
        check("2e appareil (téléphone, sans état de départ) : réunion, rien n'est effacé",
              st == 200 and body["rev"] == 2 and set(phone_base["favorites"]) == {"s1", "s2", "s9"} and set(phone_base["colors"]) == {"s1", "s9"}
              and set(phone_base["categories"]["Mèmes"]) == {"s1", "s2", "s7"} and set(phone_base["categories"]) == {"Mèmes", "Jeux"}, (st, body))
        st, body, _ = await call("POST", "/me/preferences/sync", uid=20, json={"base": pc_base, "doc": pc_base})
        check("le PC redemande sans rien changer : il reçoit ce que le téléphone a ajouté (révision inchangée)", st == 200 and body["rev"] == 2 and body["doc"] == phone_base, (st, body))
        pc_base = body["doc"]
        pc_edit = copy.deepcopy(pc_base)
        pc_edit["favorites"].remove("s2")
        pc_edit["categories"].pop("Jeux")
        pc_edit["order"] = ["Mèmes"]
        st, body, _ = await call("POST", "/me/preferences/sync", uid=20, json={"base": pc_base, "doc": pc_edit})
        check("le PC retire un favori et supprime une catégorie : c'est enregistré (révision 3)", st == 200 and body["rev"] == 3 and "s2" not in body["doc"]["favorites"] and "Jeux" not in body["doc"]["categories"], (st, body))
        phone_edit = copy.deepcopy(phone_base)
        phone_edit["favorites"].append("s5")
        phone_edit["categories"]["Jeux"].append("s4")        # le téléphone modifie une catégorie que le PC vient de supprimer
        st, body, _ = await call("POST", "/me/preferences/sync", uid=20, json={"base": phone_base, "doc": phone_edit})
        final = body["doc"]
        check("le téléphone (resté sur l'ancien état) ajoute un favori : gardé ; ce que le PC a retiré ne revient pas",
              st == 200 and "s5" in final["favorites"] and "s2" not in final["favorites"] and "Jeux" not in final["categories"], (st, body))
        st, body, _ = await call("GET", "/me/preferences", uid=20)
        check("GET renvoie la même copie", st == 200 and body["doc"] == final and body["rev"] == 4, body)

        print("\n--- Une personne ne voit que les siennes ---")
        st, body, _ = await call("GET", "/me/preferences", uid=21)
        check("une autre personne n'a rien", st == 200 and body == {"rev": 0, "doc": P.empty_doc()}, body)
        st, body, _ = await call("POST", "/me/preferences/sync", uid=21, json={"base": None, "doc": doc(["autre"])})
        st, body, _ = await call("GET", "/me/preferences", uid=20)
        check("…et son propre envoi ne touche pas aux miennes", body["doc"] == final, body)

        print("\n--- Refus ---")
        for label, payload, message in (
            ("corps qui n'est pas un objet", [], "corps JSON invalide"),
            ("document manquant", {"base": None}, None),
            ("favori invalide", {"doc": {"favorites": [7]}}, "préférences invalides"),
            ("couleur invalide", {"doc": {"colors": {"a": "bleu"}}}, "préférences invalides"),
            ("état de départ invalide", {"base": {"favorites": "x"}, "doc": {}}, "préférences invalides"),
            ("trop de favoris", {"doc": {"favorites": [f"s{i}" for i in range(P.FAVORITES_MAX + 1)]}}, "trop d'éléments dans vos préférences (favoris, couleurs, catégories)"),
        ):
            st, body, _ = await call("POST", "/me/preferences/sync", uid=20, json=payload)
            if message is None:      # « doc » absent : équivaut à un document vide, avec ce que `base` connaît → rien à supprimer sans état de départ
                check(f"{label} : accepté comme un document vide (rien n'est effacé)", st == 200 and body["doc"] == final, (st, body))
            else:
                check(f"{label} : 400 avec un message", st == 400 and body.get("error") == message, (st, body))
        st, body, _ = await call("POST", "/me/preferences/sync", uid=20, data=b"pas du json")
        check("corps qui n'est pas du JSON : 400", st == 400, (st, body))
        st, body, _ = await call("GET", "/me/preferences", uid=20)
        check("…et un envoi refusé ne change rien", body["doc"] == final, body)
        english = await call("POST", "/me/preferences/sync", uid=20, language="en", json={"doc": {"favorites": [7]}})
        check("message d'erreur dans la langue du client", english[1] == {"error": "invalid preferences"}, english[1])

        print("\n--- Supprimer tout efface la copie ---")
        everything = await call("GET", "/me/preferences", uid=21)
        st, body, _ = await call("POST", "/me/preferences/sync", uid=21, json={"base": everything[1]["doc"], "doc": P.empty_doc()})
        stored = json.loads(S.USER_PREFERENCES_PATH.read_text(encoding="utf-8"))
        check("plus rien à garder : l'entrée de la personne est supprimée du fichier", st == 200 and body["doc"] == P.empty_doc() and "21" not in stored and "20" in stored, (st, stored.keys()))
        if os.name != "nt":
            mode = S.USER_PREFERENCES_PATH.stat().st_mode & 0o777
            check("le fichier n'est lisible que par le serveur (0600)", mode == 0o600, oct(mode))

        print("\n--- Reprise du fichier de la 3.5.0 ---")
        check("l'ancien fichier a été mis de côté à la première écriture (il ne ressuscitera rien)", not S.USER_CATEGORIES_PATH.exists() and S.USER_CATEGORIES_PATH.with_name("user_categories.json.migrated").exists())
        st, body, _ = await call("GET", "/me/preferences", uid=22)
        check("ses catégories d'avant sont là", st == 200 and body["doc"]["categories"] == {"Ancienne": ["o1", "o2"]} and body["doc"]["order"] == ["Ancienne", "3101"], body)

        print("\n--- Anciennes routes de la 3.5.0 ---")
        st, body, _ = await call("GET", "/me/categories", uid=22)
        check("GET /me/categories : catégories et ordre de la copie", st == 200 and body["categories"] == {"Ancienne": ["o1", "o2"]} and body["order"] == ["Ancienne", "3101"], body)
        st, body, _ = await call("PUT", "/me/categories", uid=22, json={"categories": {"Ancienne": ["o1"], "Venue du PC": ["p1"]}, "order": ["Venue du PC", "Ancienne"]})
        check("PUT /me/categories d'un ancien client : accepté", st == 200 and body == {"ok": True, "count": 2}, (st, body))
        st, body, _ = await call("GET", "/me/preferences", uid=22)
        check("…fusionné sans rien retirer (ce que la page web a fait reste)", set(body["doc"]["categories"]["Ancienne"]) == {"o1", "o2"} and "Venue du PC" in body["doc"]["categories"], body)
        st, body, _ = await call("PUT", "/me/categories", uid=22, json={"categories": {"A": [3]}})
        check("PUT /me/categories invalide : 400", st == 400 and body.get("error") == "préférences invalides", (st, body))

    await bot._web_runner.cleanup()


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
check("les messages d'erreur sont traduits en anglais", all(M.is_translated(message) for message in ("préférences invalides", "trop d'éléments dans vos préférences (favoris, couleurs, catégories)")))
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
