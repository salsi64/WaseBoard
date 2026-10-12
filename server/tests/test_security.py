"""Sécurité de base : jeton partagé comparé à temps constant, expiration des sessions inactives, déconnexion
(POST /oauth/logout), réglage session_max_idle_days. Vrai code de server.py, faux objets de requête.
Dossier de données isolé (_env.py)."""
import asyncio
import json
import sys
import time

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402
import wb_config  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


class Req:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.query = {}
        self.match_info = {}


bot = S.bot


async def main():
    print("\n--- Jeton partagé (X-WaseBoard-Token) ---")
    S.SHARED_SECRET = "s3cret-" + "x" * 33
    check("bon jeton accepté", bot._check_auth(Req({"X-WaseBoard-Token": S.SHARED_SECRET})))
    check("mauvais jeton refusé", not bot._check_auth(Req({"X-WaseBoard-Token": "mauvais"})))
    check("jeton absent refusé", not bot._check_auth(Req()))
    check("jeton vide refusé", not bot._check_auth(Req({"X-WaseBoard-Token": ""})))
    check("préfixe du bon jeton refusé", not bot._check_auth(Req({"X-WaseBoard-Token": S.SHARED_SECRET[:-1]})))
    check("jeton plus long refusé", not bot._check_auth(Req({"X-WaseBoard-Token": S.SHARED_SECRET + "x"})))
    check("caractères non ASCII : refusé sans erreur", not bot._check_auth(Req({"X-WaseBoard-Token": "é" * 40})))
    S.SHARED_SECRET = "clé-é-à-ü" + "x" * 30
    check("secret configuré avec des accents : accepté tel quel", bot._check_auth(Req({"X-WaseBoard-Token": S.SHARED_SECRET})))
    S.SHARED_SECRET = ""
    check("aucun secret configuré : tout passe (comportement historique, signalé au démarrage)", bot._check_auth(Req()))
    S.SHARED_SECRET = "s3cret-" + "x" * 33

    print("\n--- Expiration des sessions inactives ---")
    now = 10_000_000.0
    day = 86400
    sessions = {
        "recent": {"last_used_at": now - 5 * day},
        "ancienne": {"last_used_at": now - 91 * day},
        "limite": {"last_used_at": now - 90 * day + 1},
        "sans_usage": {"created_at": now - 200 * day},
        "sans_dates": {},
    }
    expired = S.purge_idle_sessions(sessions, 90, now)
    check("supprime les sessions inactives depuis plus de 90 jours (usage ou, à défaut, création)",
          sorted(expired) == ["ancienne", "sans_usage"], sorted(expired))
    check("garde les sessions récentes, celles à la limite et celles sans date", sorted(sessions) == ["limite", "recent", "sans_dates"], sorted(sessions))
    check("0 = jamais d'expiration", S.purge_idle_sessions({"a": {"last_used_at": 0}}, 0, now) == [])
    check("réglage par défaut : 90 jours", S.SESSION_MAX_IDLE_DAYS == 90, S.SESSION_MAX_IDLE_DAYS)

    print("\n--- Déconnexion : POST /oauth/logout ---")
    bot.oauth_sessions.clear()
    bot.oauth_sessions["tok-a"] = {"user_id": "1", "username": "alice", "discord_access_token": "x",
                                   "discord_refresh_token": "y", "last_used_at": time.time()}
    bot.oauth_sessions["tok-b"] = {"user_id": "2", "username": "bob", "last_used_at": time.time()}
    S.save_oauth_sessions(bot.oauth_sessions)
    good = {"X-WaseBoard-Token": S.SHARED_SECRET}
    resp = await bot._handle_oauth_logout(Req({**good, "Authorization": "Bearer tok-a"}))
    check("répond ok", resp.status == 200 and json.loads(resp.text) == {"status": "ok"}, resp.text)
    check("la session de l'appelant est supprimée, pas celle des autres", "tok-a" not in bot.oauth_sessions and "tok-b" in bot.oauth_sessions)
    check("la suppression est écrite sur disque (jetons Discord compris)", "tok-a" not in S.load_oauth_sessions() and "tok-b" in S.load_oauth_sessions())
    resp = await bot._handle_oauth_logout(Req({**good, "Authorization": "Bearer tok-a"}))
    check("idempotent : session déjà absente -> ok", resp.status == 200)
    resp = await bot._handle_oauth_logout(Req({**good}))
    check("sans session : ok, rien supprimé", resp.status == 200 and "tok-b" in bot.oauth_sessions)
    resp = await bot._handle_oauth_logout(Req({"X-WaseBoard-Token": "mauvais", "Authorization": "Bearer tok-b"}))
    check("sans le bon jeton partagé : 401 et la session reste", resp.status == 401 and "tok-b" in bot.oauth_sessions)

    print("\n--- Réglage session_max_idle_days ---")
    bad_ok = True
    for bad in (-1, "abc", True, 5000, None):
        try:
            wb_config.validate_limits({"session_max_idle_days": bad}); bad_ok = False
        except wb_config.ConfigError:
            pass
    check("valeurs invalides refusées (négatif, texte, booléen, > 3650, null)", bad_ok)
    wb_config.validate_limits({"session_max_idle_days": 0}); wb_config.validate_limits({"session_max_idle_days": 30.5})
    cfg = wb_config.load_config(environ={"WASEBOARD_SESSION_MAX_IDLE_DAYS": "45"}, config_path=_env.DATA_DIR / "inexistant.json")
    check("variable d'environnement WASEBOARD_SESSION_MAX_IDLE_DAYS prise en compte", cfg.get("session_max_idle_days") == 45.0, cfg)


asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
