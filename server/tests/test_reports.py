"""Statistiques des commandes /top et /bilan : calculs et mise en forme purs (reports.py), sans Discord ni disque."""
import sys
from datetime import datetime

import _env  # noqa: E402  (met le dossier du serveur sur sys.path)
import reports as R  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def ts(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi).timestamp()


def rec(when, user, sound, guild="G1", name=None, emoji=None, username=None):
    return {"ts": when, "user_id": user, "username": username or f"user{user}", "sound_id": sound,
            "sound_name": name or f"son-{sound}", "emoji": emoji, "guild_id": guild}


NAMES = {"a": {"name": "Applaudissements", "emoji": "👏"}, "b": {"name": "Bruh", "emoji": "💀"}}

RECORDS = (
    [rec(ts(2026, 3, 14, 21), "1", "a")] * 5 +                       # 5 lectures le 14 mars, à 21 h
    [rec(ts(2026, 3, 14, 21, 30), "2", "a")] * 2 +
    [rec(ts(2026, 5, 2, 9), "1", "b", name="Ancien nom de Bruh")] * 3 +
    [rec(ts(2026, 5, 3, 22), "3", "gone", name="Disparu")] +            # son supprimé du catalogue
    [rec(ts(2026, 1, 1, 0, 0), "2", "b")] +                           # premier instant de l'année : compté
    [rec(ts(2025, 12, 31, 23, 59), "1", "a")] +                       # année précédente : ignoré
    [rec(ts(2027, 1, 1, 0, 0), "1", "a")] +                           # année suivante : ignoré
    [rec(ts(2026, 3, 14, 21), "1", "a", guild="G2")] * 9              # autre serveur : ignoré
)

print("\n--- Bilan annuel : calculs ---")
r = R.year_report(RECORDS, "G1", 2026, "1", NAMES)
check("seules les lectures de ce serveur et de cette année comptent", r["total_plays"] == 12, r["total_plays"])
check("1er janvier 00:00 compté, 31 décembre 23:59 et 1er janvier suivant non", r["total_plays"] == 12 and r["distinct_sounds"] == 3)
check("jours d'activité", r["active_days"] == 4, r["active_days"])
check("jour le plus chargé : le 14 mars (7 lectures)", r["busiest_day"] == {"date": "2026-03-14", "count": 7}, r["busiest_day"])
check("heure de pointe : 21 h", r["busiest_hour"] == 21, r["busiest_hour"])
check("sons : classés, nom ACTUEL du catalogue (renommé) ou nom enregistré, ou « supprimé »",
      [(s["name"], s["count"]) for s in r["top_sounds"]] == [("Applaudissements", 7), ("Bruh", 4), ("Disparu", 1)], r["top_sounds"])
check("emoji du catalogue repris", r["top_sounds"][0]["emoji"] == "👏")
check("membres classés par nombre de lectures", [(u["user_id"], u["count"]) for u in r["top_users"]] == [("1", 8), ("2", 3), ("3", 1)], r["top_users"])
me = r["me"]
check("« vous » : lectures, rang, nombre de membres, part", me["plays"] == 8 and me["rank"] == 1 and me["members"] == 3 and me["share_percent"] == 67, me)
check("« vous » : sons préférés dans l'ordre", [s["name"] for s in me["top_sounds"]] == ["Applaudissements", "Bruh"], me["top_sounds"])
check("quelqu'un qui n'a rien joué : pas de section « vous »", R.year_report(RECORDS, "G1", 2026, "999", NAMES)["me"] is None)
check("sans identité demandée : pas de section « vous »", R.year_report(RECORDS, "G1", 2026, None, NAMES)["me"] is None)

tied = [rec(ts(2026, 6, 1), "1", "a")] * 2 + [rec(ts(2026, 6, 1), "2", "a")] * 2 + [rec(ts(2026, 6, 1), "3", "a")]
check("égalité : les ex æquo partagent le même rang", R.year_report(tied, "G1", 2026, "2", NAMES)["me"]["rank"] == 1
      and R.year_report(tied, "G1", 2026, "3", NAMES)["me"]["rank"] == 3)

empty = R.year_report(RECORDS, "G1", 2020, "1", NAMES)
check("année sans lecture : zéro partout, pas de jour de pointe", empty["total_plays"] == 0 and empty["busiest_day"] is None
      and empty["busiest_hour"] is None and empty["top_sounds"] == [] and empty["me"] is None)
check("le journal peut être un générateur (lu une seule fois)", R.year_report((x for x in RECORDS), "G1", 2026, "1", NAMES)["total_plays"] == 12)
check("lignes sans date ou incomplètes ignorées sans planter", R.year_report([{}, {"guild_id": "G1"}, {"ts": "x"}][:2], "G1", 2026, "1", NAMES)["total_plays"] == 0)

print("\n--- Mise en forme ---")
stats = {"total_plays": 12, "days": 30,
         "top_sounds": [{"name": "Applaudissements", "emoji": "👏", "count": 7}, {"name": "Bruh", "emoji": None, "count": 4}],
         "top_users": [{"username": "Alice", "count": 8}, {"username": None, "count": 3}]}
view = R.render_top(stats, "Mon serveur", 30)
fields = dict(view["fields"])
check("/top : titre avec le serveur et la période", "Mon serveur" in view["title"] and "30 derniers jours" in view["title"], view["title"])
check("/top : médailles, nom en gras, nombre de lectures", fields["🔊 Sons les plus joués"].splitlines()[0] == "🥇 👏 **Applaudissements** — 7", fields)
check("/top : sans emoji, pas d'espace en trop ; 4e et suivants numérotés", "🥈 **Bruh** — 4" in fields["🔊 Sons les plus joués"])
check("/top : membre sans nom connu", "Membre inconnu" in fields["👤 Membres les plus actifs"])
check("/top : période 7 jours et 12 mois", "7 derniers jours" in R.render_top(stats, "S", 7)["title"] and "12 derniers mois" in R.render_top(stats, "S", 365)["title"])
none = R.render_top({"total_plays": 0, "top_sounds": [], "top_users": []}, "S", 30)
check("/top : aucune lecture -> message simple, pas de champs", none["fields"] == [] and "Aucun" in none["description"])

bilan = R.render_bilan(r, "Mon serveur")
bf = dict(bilan["fields"])
check("/bilan : titre avec l'année", "2026" in bilan["title"] and "Mon serveur" in bilan["title"])
check("/bilan : description chiffrée", "**12** son(s)" in bilan["description"] and "**3** son(s) différent(s)" in bilan["description"] and "**4** jour(s)" in bilan["description"], bilan["description"])
check("/bilan : « Vous » avec le rang et les sons préférés", "**#1** sur 3" in bf["🙋 Vous"] and "Applaudissements" in bf["🙋 Vous"], bf["🙋 Vous"])
check("/bilan : temps forts en français", "14 mars" in bf["📅 Temps forts"] and "21 h – 22 h" in bf["📅 Temps forts"], bf["📅 Temps forts"])
check("/bilan : sans participation de la personne, pas de « Vous »",
      "🙋 Vous" not in dict(R.render_bilan(R.year_report(RECORDS, "G1", 2026, "999", NAMES), "S")["fields"]))
late = R.year_report([rec(ts(2026, 1, 1, 23, 30), "1", "a"), rec(ts(2026, 1, 1, 8), "1", "a")], "G1", 2026, "1", NAMES)
late_text = dict(R.render_bilan(late, "S")["fields"])["📅 Temps forts"]
check("1er du mois en « 1er », heure de pointe 23 h → 0 h bien formée", "1er janvier" in late_text and "23 h – 0 h" in late_text, late_text)
check("/bilan : année vide -> message simple", R.render_bilan(empty, "S")["fields"] == [] and "2020" in R.render_bilan(empty, "S")["description"])

print("\n--- Texte sûr : un nom ne peut ni casser la mise en forme ni mentionner quelqu'un ---")
rows = [{"name": "*gras* _it_ `code` @everyone <@123>", "emoji": None, "count": 1}, {"name": "[x](y)", "emoji": None, "count": 1}]
line = R._sound_lines(rows)
check("Markdown échappé", "\\*gras\\*" in line and "\\_it\\_" in line and "\\`code\\`" in line and "\\[x\\]\\(y\\)" in line, line)
check("@everyone et <@id> neutralisés", "@everyone" not in line and "<@123>" not in line, line)
check("nom trop long tronqué", len(R.escape_text("x" * 500)) <= R.MAX_NAME + 2 and R.escape_text("x" * 500).endswith("…"))
check("sauts de ligne aplatis", "\n" not in R.escape_text("a\nb"))
check("None toléré", R.escape_text(None) == "")
many = [{"name": "N" * 60, "emoji": "🔥", "count": 99999} for _ in range(10)]
check("dix lignes de noms très longs tiennent dans un champ Discord (1024)", len(R._sound_lines(many)) <= 1024, len(R._sound_lines(many)))

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
