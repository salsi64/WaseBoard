"""Journal d'accès : les sondages réguliers de l'appli qui réussissent ne sont plus journalisés (QuietPollingFilter) ;
tout le reste (échecs, lectures, uploads...) l'est toujours. Dossier de données isolé (_env.py)."""
import logging
import sys

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def logged(line):
    record = logging.LogRecord("aiohttp.access", logging.INFO, __file__, 1, line, None, None)
    return all(f.filter(record) for f in logging.getLogger("aiohttp.access").filters)


ACCESS = '127.0.0.1 [12/Oct/2026:18:00:00 +0200] "{req}" {status} 563 "-" "-"'

check("le filtre est bien branché sur le journal d'accès d'aiohttp",
      any(isinstance(f, S.QuietPollingFilter) for f in logging.getLogger("aiohttp.access").filters))
check("GET /activity -> 200 : plus journalisé", not logged(ACCESS.format(req="GET /activity HTTP/1.1", status=200)))
check("GET /status?strict=1 -> 200 : plus journalisé", not logged(ACCESS.format(req="GET /status?strict=1 HTTP/1.1", status=200)))
check("GET /status -> 200 : plus journalisé", not logged(ACCESS.format(req="GET /status HTTP/1.1", status=200)))
check("GET /activity?guild_id=… -> 200 (page web, Activité) : plus journalisé", not logged(ACCESS.format(req="GET /activity?guild_id=1234567890 HTTP/1.1", status=200)))
check("POST /me/preferences/sync -> 200 : plus journalisé", not logged(ACCESS.format(req="POST /me/preferences/sync HTTP/1.1", status=200)))
check("POST /me/preferences/sync -> 400 : journalisé (un refus reste visible)", logged(ACCESS.format(req="POST /me/preferences/sync HTTP/1.1", status=400)))
check("POST /me/preferences/sync -> 401 : journalisé", logged(ACCESS.format(req="POST /me/preferences/sync HTTP/1.1", status=401)))
check("GET /activity?guild_id=… -> 401 : journalisé", logged(ACCESS.format(req="GET /activity?guild_id=1 HTTP/1.1", status=401)))
check("GET /me/preferences -> 200 : journalisé (lecture ponctuelle)", logged(ACCESS.format(req="GET /me/preferences HTTP/1.1", status=200)))
check("une autre route qui commence pareil reste journalisée", logged(ACCESS.format(req="GET /activity-log HTTP/1.1", status=200)) and logged(ACCESS.format(req="GET /statuses HTTP/1.1", status=200)))
check("GET /activity -> 401 : journalisé (un échec reste visible)", logged(ACCESS.format(req="GET /activity HTTP/1.1", status=401)))
check("GET /status -> 503 : journalisé", logged(ACCESS.format(req="GET /status HTTP/1.1", status=503)))
check("POST /play -> 200 : journalisé", logged(ACCESS.format(req="POST /play HTTP/1.1", status=200)))
check("GET /sounds -> 200 : journalisé", logged(ACCESS.format(req="GET /sounds HTTP/1.1", status=200)))
check("POST /sounds -> 200 (upload) : journalisé", logged(ACCESS.format(req="POST /sounds HTTP/1.1", status=200)))
check("GET /health -> 200 : journalisé (la sonde externe reste visible)", logged(ACCESS.format(req="GET /health HTTP/1.1", status=200)))
check("un message du serveur lui-même n'est jamais filtré",
      logging.getLogger("waseboard-server").filters == [] or all(not isinstance(f, S.QuietPollingFilter) for f in logging.getLogger("waseboard-server").filters))

# ---------- échecs répétés : regroupés (un client à clé refusée n'écrit plus 3 lignes par seconde) ----------
class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


def fresh():
    clock = Clock()
    flt = S.QuietPollingFilter(clock=clock)

    def run(req, status):
        record = logging.LogRecord("aiohttp.access", logging.INFO, __file__, 1, ACCESS.format(req=req, status=status), None, None)
        return flt.filter(record), record.getMessage()
    return flt, clock, run


flt, clock, run = fresh()
first = run("GET /activity HTTP/1.1", 401)
check("1er refus d'un sondage : journalisé tel quel (aucune annotation)", first[0] and "identiques" not in first[1], first)
repeated = [run("GET /activity HTTP/1.1", 401)[0] for _ in range(190)]
check("les refus identiques suivants, dans la minute : comptés, plus journalisés", not any(repeated))
clock.now += 59
check("à 59 s : toujours silencieux", not run("GET /activity HTTP/1.1", 401)[0])
clock.now += 2
ok, text = run("GET /activity HTTP/1.1", 401)
check("après une minute : une ligne, qui dit combien de refus ont été passés sous silence (190 + 1 à 59 s = 191)", ok and "[+191 identiques non journalisées" in text, text)
ok, text = run("GET /activity HTTP/1.1", 401)
check("…puis le compteur repart de zéro (fenêtre suivante)", not ok)
clock.now += 61
ok, text = run("GET /activity HTTP/1.1", 401)
check("fenêtre suivante : le compte ne reprend que ses propres refus", ok and "[+1 identiques non journalisées" in text, text)

flt, clock, run = fresh()
run("GET /activity HTTP/1.1", 401)
check("autre code sur la même route (503) : suivi à part, journalisé", run("GET /activity HTTP/1.1", 503)[0])
check("autre route (/status) : suivie à part, journalisée", run("GET /status?strict=1 HTTP/1.1", 401)[0])
check("avec ou sans paramètres (?guild_id=…) : deux sondages distincts, chacun journalisé une fois", run("GET /activity?guild_id=1 HTTP/1.1", 401)[0] and not run("GET /activity?guild_id=2 HTTP/1.1", 401)[0])
check("POST /me/preferences/sync -> 401 : journalisé une fois, puis regroupé", run("POST /me/preferences/sync HTTP/1.1", 401)[0] and not run("POST /me/preferences/sync HTTP/1.1", 401)[0])
check("une réponse réussie reste silencieuse, même au milieu d'un flot de refus", not run("GET /activity HTTP/1.1", 200)[0])

for _ in range(1000):
    assert run("POST /play HTTP/1.1", 401)[0] and run("GET /sounds HTTP/1.1", 401)[0] and run("GET /health HTTP/1.1", 500)[0]
check("hors sondages : chaque requête reste journalisée une à une, même répétée (1000 refus de /play, /sounds, /health)", True)

flt, clock, run = fresh()
for status in range(100, 1000):
    for route in ("GET /activity HTTP/1.1", "GET /status HTTP/1.1", "POST /me/preferences/sync HTTP/1.1"):
        run(route, status)
check("mémoire bornée : jamais plus de 200 types d'échec suivis, même avec tous les codes possibles", len(flt._failures) <= 200, len(flt._failures))

import time as _time
flt, clock, run = fresh()
record200 = logging.LogRecord("aiohttp.access", logging.INFO, __file__, 1, ACCESS.format(req="GET /activity HTTP/1.1", status=200), None, None)
recordPlay = logging.LogRecord("aiohttp.access", logging.INFO, __file__, 1, ACCESS.format(req="POST /play HTTP/1.1", status=200), None, None)
record401 = logging.LogRecord("aiohttp.access", logging.INFO, __file__, 1, ACCESS.format(req="GET /activity HTTP/1.1", status=401), None, None)
start = _time.perf_counter()
for _ in range(100_000):
    flt.filter(record200); flt.filter(recordPlay); flt.filter(record401)
per_line_us = (_time.perf_counter() - start) / 300_000 * 1e6
check(f"coût du filtre : {per_line_us:.1f} µs par ligne (sondage réussi, lecture, refus répété) — négligeable devant une requête", per_line_us < 50, per_line_us)

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
