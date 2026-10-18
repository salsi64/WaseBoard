"""Arrêt propre du serveur (systemctl stop/restart) : le bot quitte ses salons vocaux puis ferme sa connexion à Discord avec des délais bornés, un
salon vocal qui ne répond pas ne retarde plus l'arrêt de 30 s, deux arrêts simultanés (signal, puis fin de bot.run) n'en font qu'un, et SIGTERM déclenche
bien cet arrêt (Linux). Faux salons vocaux et faux client Discord. Dossier de données isolé (_env.py)."""
import asyncio
import os
import signal
import sys
import time

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


S.SHUTDOWN_VOICE_TIMEOUT_S = 0.3
S.SHUTDOWN_CLOSE_TIMEOUT_S = 0.4
bot = S.bot


class FakeVoice:
    def __init__(self, channel_id, hangs=False, fails=False):
        self.channel = type("Channel", (), {"id": channel_id})()
        self.hangs, self.fails = hangs, fails
        self.disconnected = False

    async def disconnect(self, force=False):
        if self.hangs:
            await asyncio.sleep(30)          # Discord ne répond pas
        if self.fails:
            raise RuntimeError("boum")
        self.disconnected = force


VOICES = []
S.WaseBoardServer.voice_clients = property(lambda self: list(VOICES))
CLOSED = {"calls": 0, "hang": False}


async def fake_client_close(self):
    CLOSED["calls"] += 1
    if CLOSED["hang"]:
        await asyncio.sleep(30)


S.discord.Client.close = fake_client_close


def fresh_state():
    bot._shutdown_started = False
    bot._shutdown_done = asyncio.Event()
    CLOSED["calls"], CLOSED["hang"] = 0, False
    VOICES.clear()


async def main():
    print("--- Départ des salons vocaux ---")
    fresh_state()
    started = time.monotonic()
    await bot._leave_voice_channels()
    check("aucun salon vocal : rien à faire, immédiat", time.monotonic() - started < 0.1)

    fresh_state()
    a, b = FakeVoice(1), FakeVoice(2)
    VOICES.extend([a, b])
    started = time.monotonic()
    await bot._leave_voice_channels()
    check("deux salons qui répondent : quittés (avec force)", a.disconnected and b.disconnected)
    check("…et sans attente inutile", time.monotonic() - started < 0.2)

    fresh_state()
    ok, stuck, broken = FakeVoice(1), FakeVoice(2, hangs=True), FakeVoice(3, fails=True)
    VOICES.extend([stuck, ok, broken])
    started = time.monotonic()
    await bot._leave_voice_channels()
    took = time.monotonic() - started
    check("un salon qui ne répond pas : abandonné au bout du délai, pas des 30 s de Discord", 0.25 < took < 1.0, f"{took:.2f} s")
    check("…les autres salons sont quand même quittés", ok.disconnected)
    check("…et une erreur de départ n'interrompt pas l'arrêt", True)

    print("\n--- Arrêt complet ---")
    fresh_state()
    started = time.monotonic()
    await bot.close()
    check("sans salon vocal : arrêt immédiat, la connexion à Discord est fermée", time.monotonic() - started < 0.2 and CLOSED["calls"] == 1)

    fresh_state()
    VOICES.extend([FakeVoice(1, hangs=True), FakeVoice(2)])
    CLOSED["hang"] = True
    started = time.monotonic()
    await bot.close()
    took = time.monotonic() - started
    check("salon vocal ET fermeture de Discord qui ne répondent pas : arrêt borné (≈ 0,7 s ici, jamais 30 s)", took < 1.5, f"{took:.2f} s")
    check("…l'arrêt est bien marqué terminé", bot._shutdown_done.is_set())

    fresh_state()
    VOICES.append(FakeVoice(1, hangs=True))
    first = asyncio.ensure_future(bot.close())
    await asyncio.sleep(0.05)
    second = asyncio.ensure_future(bot.close())             # fin de bot.run pendant l'arrêt demandé par le signal
    await asyncio.gather(first, second)
    check("deux arrêts en même temps n'en font qu'un (Discord n'est fermé qu'une fois)", CLOSED["calls"] == 1, CLOSED["calls"])
    started = time.monotonic()
    await bot.close()
    check("un arrêt de plus, après coup : rend la main tout de suite", time.monotonic() - started < 0.1)

    print("\n--- SIGTERM ---")
    if os.name == "nt":
        print("SKIP  SIGTERM (Windows : pas de gestionnaire de signaux dans la boucle)")
    else:
        fresh_state()
        asked = asyncio.Event()

        async def fake_close():
            asked.set()

        real_close = bot.close
        bot.close = fake_close
        bot._install_signal_handlers()
        os.kill(os.getpid(), signal.SIGTERM)
        try:
            await asyncio.wait_for(asked.wait(), 2)
            check("SIGTERM déclenche l'arrêt propre (close) au lieu de lever KeyboardInterrupt", True)
        except asyncio.TimeoutError:
            check("SIGTERM déclenche l'arrêt propre (close) au lieu de lever KeyboardInterrupt", False, "close non appelé")
        finally:
            asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)
            bot.close = real_close


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
