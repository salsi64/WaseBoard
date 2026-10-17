"""Préférences personnelles : favoris, couleurs des sons, catégories personnelles et leur ordre.

Elles vivent à trois endroits à la fois : l'application Windows (réglages locaux), la page web et l'Activité Discord. Le serveur garde
une copie PAR PERSONNE et fusionne ce que chacun envoie, pour que rien ne se perde quand on modifie depuis deux endroits.

Le principe est celui d'une fusion « à trois voies » : chaque appareil envoie son état actuel (`mine`) ET l'état du serveur qu'il avait
reçu la dernière fois (`base`). Ce qui a changé entre `base` et `mine` est ce que la personne vient de faire ; ce qui a changé entre `base`
et l'état actuel du serveur (`current`) est ce qu'un autre appareil a fait entre-temps. Le résultat garde les deux (un ajout n'écrase pas un
autre ajout, une suppression est conservée), et renvoyer deux fois la même chose ne change rien. Sans `base` (première synchronisation),
tout ce que l'appareil envoie compte comme un ajout : on n'efface jamais rien sur la foi d'un état dont on ne connaît pas l'origine.

Un document est { favorites: [ids de sons], colors: {id de son: "#RRGGBB"}, categories: {nom: [ids de sons]},
order: [noms de catégories et identifiants de serveurs Discord, dans l'ordre d'affichage de l'application] }.
"""
import re
from typing import Optional

FAVORITES_MAX = 5000          # sons favoris
COLORS_MAX = 5000             # sons avec une couleur choisie
CATEGORIES_MAX = 200          # catégories par personne
CATEGORY_NAME_MAX = 100       # caractères dans un nom de catégorie
CATEGORY_SOUNDS_MAX = 5000    # sons par catégorie
ORDER_MAX = 1000              # entrées dans l'ordre d'affichage (catégories + identifiants de serveurs Discord)
ID_MAX = 200                  # caractères dans un identifiant (de son ou de serveur)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def empty_doc() -> dict:
    return {"favorites": [], "colors": {}, "categories": {}, "order": []}


def is_empty(doc: dict) -> bool:
    return not (doc["favorites"] or doc["colors"] or doc["categories"] or doc["order"])


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def _valid_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= ID_MAX


def normalize(value) -> tuple[Optional[dict], Optional[str]]:
    """Nettoie un document reçu d'un appareil : (document, None) ou (None, motif du refus : "format" ou "limit"). Une valeur absente
    (None) ou un champ manquant vaut « vide » ; les doublons sont retirés (l'ordre est gardé), les noms rognés, les couleurs mises en majuscules."""
    if value is None:
        return empty_doc(), None
    if not isinstance(value, dict):
        return None, "format"
    favorites, colors = value.get("favorites", []), value.get("colors", {})
    categories, order = value.get("categories", {}), value.get("order", [])
    if not (isinstance(favorites, list) and isinstance(colors, dict) and isinstance(categories, dict) and isinstance(order, list)):
        return None, "format"
    if len(favorites) > FAVORITES_MAX or len(colors) > COLORS_MAX or len(categories) > CATEGORIES_MAX or len(order) > ORDER_MAX:
        return None, "limit"
    if not all(_valid_id(i) for i in favorites) or not all(_valid_id(k) for k in order):
        return None, "format"

    clean_colors = {}
    for sound_id, color in colors.items():
        if not _valid_id(sound_id) or not isinstance(color, str) or not _HEX.match(color):
            return None, "format"
        clean_colors[sound_id] = color.upper()

    clean_categories: dict[str, list[str]] = {}
    for name, ids in categories.items():
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > CATEGORY_NAME_MAX or not isinstance(ids, list) or not all(_valid_id(i) for i in ids):
            return None, "format"
        if len(ids) > CATEGORY_SOUNDS_MAX:
            return None, "limit"
        clean_categories[name] = _unique(clean_categories.get(name, []) + ids)   # deux noms identiques après nettoyage : fusionnés
    return {"favorites": _unique(favorites), "colors": clean_colors, "categories": clean_categories, "order": _unique(order)}, None


def _merge_lists(base: list, mine: list, current: list) -> list:
    """Liste vue comme un ensemble : ce que le serveur a, moins ce que j'ai retiré, plus ce que j'ai ajouté (l'ordre du serveur d'abord)."""
    base_set, mine_set = set(base), set(mine)
    removed_by_me = base_set - mine_set
    kept = [item for item in current if item not in removed_by_me]
    present = set(kept)
    return kept + [item for item in mine if item not in base_set and item not in present]


def _merge_colors(base: dict, mine: dict, current: dict) -> dict:
    """Ce que j'ai changé (ajouté, modifié ou retiré) l'emporte ; le reste vient du serveur."""
    result = dict(current)
    for sound_id in set(base) | set(mine):
        if mine.get(sound_id) == base.get(sound_id):
            continue
        if sound_id in mine:
            result[sound_id] = mine[sound_id]
        else:
            result.pop(sound_id, None)
    return result


def _merge_categories(base: dict, mine: dict, current: dict) -> dict:
    """Une catégorie supprimée par l'un ou l'autre disparaît ; une catégorie créée par l'un ou l'autre est gardée ; le contenu de chacune
    est fusionné comme un ensemble. Un renommage est une suppression suivie d'une création : le contenu suit."""
    removed_by_me = set(base) - set(mine)
    names = [name for name in current if name not in removed_by_me]
    names += [name for name in mine if name not in base and name not in current]
    return {name: _merge_lists(base.get(name, []), mine.get(name, []), current.get(name, [])) for name in names}


def merge(base: Optional[dict], mine: dict, current: dict) -> dict:
    """Fusion à trois voies (voir l'en-tête du module). Les trois documents sont déjà passés par `normalize`."""
    base = base or empty_doc()
    categories = _merge_categories(base["categories"], mine["categories"], current["categories"])
    # Ordre d'affichage : celui de l'appareil qui vient de le changer ; sinon celui du serveur. Les catégories disparues en sortent,
    # les nouvelles s'ajoutent à la fin ; les identifiants de serveurs (inconnus d'ici) restent tels quels.
    order = mine["order"] if mine["order"] != base["order"] else current["order"]
    gone = (set(base["categories"]) | set(mine["categories"]) | set(current["categories"])) - set(categories)
    order = [key for key in order if key not in gone]
    order += [name for name in categories if name not in order]
    return clamp({
        "favorites": _merge_lists(base["favorites"], mine["favorites"], current["favorites"]),
        "colors": _merge_colors(base["colors"], mine["colors"], current["colors"]),
        "categories": categories,
        "order": _unique(order),
    })


def clamp(doc: dict) -> dict:
    """Ramène un document dans les limites (la réunion de deux documents permis peut les dépasser) : on garde les premiers éléments."""
    return {
        "favorites": doc["favorites"][:FAVORITES_MAX],
        "colors": dict(list(doc["colors"].items())[:COLORS_MAX]),
        "categories": {name: ids[:CATEGORY_SOUNDS_MAX] for name, ids in list(doc["categories"].items())[:CATEGORIES_MAX]},
        "order": doc["order"][:ORDER_MAX],
    }
