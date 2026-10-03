"""Cache de find_memberships (MEMBERSHIP_CACHE_TTL) : évite de marteler guild.fetch_member (vrai
appel REST Discord, soumis à sa limite de débit) à chaque clic/sondage rapide d'un même utilisateur.
Vrai code de server.py (bot.find_memberships n'est PAS monkeypatché ici, contrairement aux autres
tests), faux objets Discord. Lancé via tests/run_all.sh (dossier de données isolé, voir _env.py)."""
import asyncio
import sys

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


class FakeMember:
    def __init__(self, uid):
        self.id = uid


class FakeResponse:
    """discord.HTTPException lit response.status/.reason dans son __init__ et son message."""
    status = 404
    reason = "Not Found"


class FakeGuild:
    """get_member renvoie toujours None (comme un membre absent du cache de la passerelle) :
    force find_memberships à passer par le chemin coûteux (fetch_member) à chaque appel non mis
    en cache, pour bien mesurer l'effet du cache."""

    def __init__(self, gid, members_present):
        self.id = gid
        self._members_present = members_present
        self.fetch_calls = 0

    def get_member(self, uid):
        return None

    async def fetch_member(self, uid):
        self.fetch_calls += 1
        if uid in self._members_present:
            return FakeMember(uid)
        raise S.discord.NotFound(response=FakeResponse(), message="introuvable")


bot = S.bot
G1 = FakeGuild(1001, members_present={42})
G2 = FakeGuild(1002, members_present={42})
S.WaseBoardServer.guilds = property(lambda self: [G1, G2])
bot._membership_cache.clear()


async def main():
    print("--- premier appel : pas encore en cache ---")
    found = await bot.find_memberships(42)
    check("trouve les 2 guildes", {g.id for g, _ in found} == {1001, 1002}, found)
    check("un fetch_member par guilde (cache froid)", (G1.fetch_calls, G2.fetch_calls) == (1, 1),
          (G1.fetch_calls, G2.fetch_calls))

    print("\n--- appels suivants rapprochés : servis par le cache ---")
    for _ in range(5):
        await bot.find_memberships(42)
    check("aucun fetch_member supplémentaire (cache chaud)", (G1.fetch_calls, G2.fetch_calls) == (1, 1),
          (G1.fetch_calls, G2.fetch_calls))

    print("\n--- un AUTRE utilisateur n'est pas affecté par le cache du premier ---")
    found_other = await bot.find_memberships(99)
    check("pas membre : liste vide", found_other == [])
    check("a bien retenté (pas de fuite de cache entre utilisateurs)",
          (G1.fetch_calls, G2.fetch_calls) == (2, 2), (G1.fetch_calls, G2.fetch_calls))

    print("\n--- expiration (TTL) ---")
    ts, cached_value = bot._membership_cache[42]
    bot._membership_cache[42] = (ts - S.MEMBERSHIP_CACHE_TTL - 1, cached_value)
    await bot.find_memberships(42)
    check("cache expiré -> re-interroge Discord", (G1.fetch_calls, G2.fetch_calls) == (3, 3),
          (G1.fetch_calls, G2.fetch_calls))

    print("\n--- garde-fou mémoire : purge des entrées expirées au-delà de 500 ---")
    bot._membership_cache.clear()
    now = S.time.monotonic()
    for uid in range(600):
        bot._membership_cache[uid] = (now - S.MEMBERSHIP_CACHE_TTL - 1, [])  # toutes déjà expirées
    await bot.find_memberships(42)  # déclenche la purge opportuniste (taille > 500)
    check("les entrées expirées ont été purgées (ne reste que celle qu'on vient d'écrire)",
          len(bot._membership_cache) == 1 and 42 in bot._membership_cache, len(bot._membership_cache))


asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
