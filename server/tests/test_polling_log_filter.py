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
check("GET /activity -> 401 : journalisé (un échec reste visible)", logged(ACCESS.format(req="GET /activity HTTP/1.1", status=401)))
check("GET /status -> 503 : journalisé", logged(ACCESS.format(req="GET /status HTTP/1.1", status=503)))
check("POST /play -> 200 : journalisé", logged(ACCESS.format(req="POST /play HTTP/1.1", status=200)))
check("GET /sounds -> 200 : journalisé", logged(ACCESS.format(req="GET /sounds HTTP/1.1", status=200)))
check("POST /sounds -> 200 (upload) : journalisé", logged(ACCESS.format(req="POST /sounds HTTP/1.1", status=200)))
check("GET /health -> 200 : journalisé (la sonde externe reste visible)", logged(ACCESS.format(req="GET /health HTTP/1.1", status=200)))
check("un message du serveur lui-même n'est jamais filtré",
      logging.getLogger("waseboard-server").filters == [] or all(not isinstance(f, S.QuietPollingFilter) for f in logging.getLogger("waseboard-server").filters))

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
