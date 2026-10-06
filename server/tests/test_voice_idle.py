"""Voyant « en train de parler » du bot : le lecteur vocal est mis en pause quand rien ne joue et relancé dès qu'un son
arrive (MixingAudioSource.attach), sans jamais bloquer un son ; plus l'annonce des fonctionnalités par /status.
Vrai code de server.py, faux client vocal : aucun jeton ni Discord réel. Dossier de données isolé (_env.py)."""
import asyncio
import json
import shutil
import sys
import threading
import time

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


FRAME = S.FRAME_BYTES


class FakeVC:
    """Imite VoiceClient.pause()/resume() : un état « en pause » et le journal des appels."""
    def __init__(self, fail_pause=False):
        self.paused = False
        self.calls = []
        self.fail_pause = fail_pause
        self.played = None

    def pause(self):
        if self.fail_pause:
            raise RuntimeError("pause impossible")
        self.paused = True
        self.calls.append("pause")

    def resume(self):
        self.paused = False
        self.calls.append("resume")

    def play(self, source):
        self.played = source

    def is_connected(self):
        return True

    async def move_to(self, channel):
        pass


class Tone(S.discord.AudioSource):
    """Source de `frames` trames non nulles, puis fin de flux."""
    def __init__(self, frames):
        self.left = frames

    def read(self):
        if self.left <= 0:
            return b""
        self.left -= 1
        return b"\x01\x00" * (FRAME // 2)

    def cleanup(self):
        pass


def idle(mixer, n):
    for _ in range(n):
        mixer.read()


async def main():
    N = S.MixingAudioSource.IDLE_FRAMES_BEFORE_SLEEP

    print("\n--- Sans client vocal lié ---")
    m = S.MixingAudioSource()
    idle(m, N * 3)
    check("aucun client lié : lecture du silence sans erreur", m.read() == S.SILENCE)

    print("\n--- Mise en pause quand rien ne joue ---")
    vc = FakeVC(); m = S.MixingAudioSource(); m.attach(vc)
    idle(m, N - 1)
    check(f"{N - 1} trames de silence : pas encore en pause", vc.calls == [], vc.calls)
    m.read()
    check(f"{N}e trame de silence : le lecteur est mis en pause (le voyant s'éteint)", vc.calls == ["pause"] and vc.paused, vc.calls)
    idle(m, 500)
    check("ensuite : une seule pause, pas une par trame", vc.calls == ["pause"], vc.calls)

    print("\n--- Reprise à l'arrivée d'un son ---")
    m.add("a", Tone(3))
    check("add() relance le lecteur immédiatement (resume appelé avant la trame suivante)", vc.calls == ["pause", "resume"] and not vc.paused, vc.calls)
    first = m.read()
    check("la première trame lue est bien celle du son (aucune trame perdue)", first[:2] == b"\x01\x00" and len(first) == FRAME, first[:4])
    m.add("b", Tone(1))
    check("un second son pendant la lecture : pas de reprise superflue", vc.calls == ["pause", "resume"], vc.calls)
    m.add("a", Tone(2))  # relance du MÊME son
    check("relancer le même son : pas de reprise superflue", vc.calls == ["pause", "resume"], vc.calls)

    print("\n--- Pas de pause tant qu'un son joue, pause une fois fini ---")
    for _ in range(3):
        m.read()
    check("pendant la lecture : jamais en pause", not vc.paused and vc.calls == ["pause", "resume"], vc.calls)
    idle(m, N + 2)
    check("fin des sons : en pause à nouveau", vc.paused and vc.calls == ["pause", "resume", "pause"], vc.calls)

    print("\n--- Stop tout ---")
    m.add("c", Tone(1000))
    check("nouveau son après une pause : reprise", not vc.paused and vc.calls[-1] == "resume", vc.calls)
    m.clear()
    idle(m, N)
    check("après clear() (Stop tout) : pause", vc.paused, vc.calls)

    print("\n--- Course add()/read() : un son n'est jamais bloqué par une pause ---")
    vc = FakeVC(); m = S.MixingAudioSource(); m.attach(vc)
    violations, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            data = m.read()
            if data != S.SILENCE and vc.paused:
                violations.append("audio lu pendant la pause")
            time.sleep(0)

    t = threading.Thread(target=reader); t.start()
    stuck = 0
    for i in range(300):
        time.sleep(0.001 * (i % 7))
        m.add(f"s{i % 5}", Tone(2))
        # le son doit être consommé : il ne peut pas rester bloqué dans un lecteur en pause
        deadline = time.time() + 2
        while time.time() < deadline and any(src.left > 0 for src, _ in list(m._entries.values())):
            time.sleep(0.001)
        if any(src.left > 0 for src, _ in list(m._entries.values())):
            stuck += 1
    stop.set(); t.join()
    check("300 ajouts à des instants variés : aucun son bloqué, aucun audio lu en pause", stuck == 0 and not violations, (stuck, violations[:2]))

    print("\n--- Échec de la pause ---")
    vc = FakeVC(fail_pause=True); m = S.MixingAudioSource(); m.attach(vc)
    idle(m, N * 2 + 1)
    m.add("x", Tone(1))
    check("pause impossible : ne casse rien, le son joue quand même", m.read()[:2] == b"\x01\x00")

    print("\n--- join_guild_voice lie le mixeur ---")
    class Channel:
        id = 1
        async def connect(self):
            return vc2
    vc2 = FakeVC()
    got = await S.bot.join_guild_voice(4242, Channel())
    mixer = S.bot.mixers[4242]
    check("le mixeur créé à la connexion est lié au client vocal et lu par lui",
          got is vc2 and mixer._voice_client is vc2 and vc2.played is mixer)

    print("\n--- /status annonce les fonctionnalités ---")
    S.bot.voice_clients_map.pop(4242, None); S.bot.mixers.pop(4242, None)  # pas de salon connecté pour /status
    class Req:
        headers = {"X-WaseBoard-Token": S.SHARED_SECRET}
        query = {}
        match_info = {}
    resp = await S.bot._handle_status(Req())
    payload = json.loads(resp.text)
    check("/status : features contient replace_file", resp.status == 200 and "replace_file" in payload.get("features", []), payload)
    legacy = {"connected", "channel", "guild_id", "guild_name", "connected_guild_count", "channel_members"}
    check("/status : tous les champs historiques sont toujours là (anciens clients)", legacy <= set(payload), set(payload))


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir()
asyncio.run(main())
print("\nRÉSULTAT :", "TOUT PASSE" if all(results) else f"{results.count(False)} échec(s) sur {len(results)}", f"({len(results)} vérifications)")
sys.exit(0 if all(results) else 1)
