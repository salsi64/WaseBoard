"""Voyant « en train de parler » du bot : le lecteur vocal est mis en pause quand rien ne joue et relancé dès qu'un son
arrive (MixingAudioSource.attach), sans jamais bloquer un son ; l'horodatage RTP rattrape le temps de la pause (sinon
chaque clic après un silence arrive en retard côté Discord) ; désactivé par défaut ; plus l'annonce des fonctionnalités
par /status. Vrai code de server.py et vrai AudioPlayer de discord.py, faux client vocal : aucun jeton ni Discord réel.
Dossier de données isolé (_env.py)."""
import asyncio
import json
import shutil
import sys
import threading
import time
import types
from pathlib import Path

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
    N = 10  # trames de 20 ms sans son avant la pause, pour ces tests (le réglage réel est voice_idle_sleep_s)

    print("\n--- Sans client vocal lié ---")
    m = S.MixingAudioSource(idle_sleep_frames=N)
    idle(m, N * 3)
    check("aucun client lié : lecture du silence sans erreur", m.read() == S.SILENCE)

    print("\n--- Mise en pause quand rien ne joue ---")
    vc = FakeVC(); m = S.MixingAudioSource(idle_sleep_frames=N); m.attach(vc)
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
    vc = FakeVC(); m = S.MixingAudioSource(idle_sleep_frames=N); m.attach(vc)
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

    print("\n--- Désactivé par défaut : flux continu, jamais de pause ---")
    check("réglage par défaut : voice_idle_sleep_s = 0", S.VOICE_IDLE_SLEEP_S == 0, S.VOICE_IDLE_SLEEP_S)
    vc = FakeVC(); m = S.MixingAudioSource(); m.attach(vc)
    idle(m, 5000)
    m.add("a", Tone(2))
    check("100 s de silence puis un son : aucune pause ni reprise (le premier son part sans latence)", vc.calls == [], vc.calls)
    check("sans réglage : jamais de pause (0 trame)", S.MixingAudioSource()._sleep_after == 0)

    print("\n--- Réveil : l'horodatage RTP rattrape le temps écoulé ---")
    clock = [1000.0]
    real_monotonic = S.time.monotonic
    S.time.monotonic = lambda: clock[0]
    try:
        def paused_mixer(start_ts):
            v = FakeVC(); v.timestamp = start_ts
            mx = S.MixingAudioSource(idle_sleep_frames=N); mx.attach(v)
            idle(mx, N)  # la N-ième trame déclenche la pause ; repère : start_ts à t = 1000 s
            v.timestamp += 960 * 6  # le lecteur envoie la trame de silence puis les 5 trames de fin de flux
            return v, mx

        vc, m = paused_mixer(1000)
        clock[0] += 10.0
        m.add("a", Tone(3))
        check("à la reprise, avant la 1re trame : horodatage inchangé", vc.timestamp == 1000 + 5760, vc.timestamp)
        frame = m.read()
        check("la 1re trame est intacte", frame[:2] == b"\x01\x00" and len(frame) == FRAME)
        check("l'horodatage a avancé des 10 s de pause : 1000 + 10 x 48000", vc.timestamp == 1000 + 480000, vc.timestamp)
        m.read()
        check("trames suivantes : une seule resynchronisation", vc.timestamp == 1000 + 480000, vc.timestamp)

        vc, m = paused_mixer(1000)
        clock[0] += 0.05  # 50 ms : moins que les trames déjà envoyées à la pause (120 ms) -> rien à rattraper
        m.add("a", Tone(2)); m.read()
        check("pause très courte : horodatage inchangé (jamais de retour en arrière)", vc.timestamp == 1000 + 5760, vc.timestamp)

        start = 0xFFFFFFFF - 1000
        vc, m = paused_mixer(start)
        vc.timestamp &= 0xFFFFFFFF
        clock[0] += 5.0
        m.add("a", Tone(2)); m.read()
        check("débordement 32 bits : l'horodatage reboucle comme un compteur RTP", vc.timestamp == (start + 240000) & 0xFFFFFFFF, vc.timestamp)

        vc = FakeVC(); m = S.MixingAudioSource(idle_sleep_frames=N); m.attach(vc)  # faux client SANS horodatage
        idle(m, N); clock[0] += 3.0
        m.add("a", Tone(1))
        check("client sans horodatage lisible : aucune erreur, le son joue", m.read()[:2] == b"\x01\x00" and vc.calls == ["pause", "resume"], vc.calls)

        vc, m = paused_mixer(500)
        m.add("a", Tone(1)); m.read()  # reprise immédiate : 0 s
        idle(m, N + 1)  # le son est fini : nouvelle pause, nouveau repère
        vc.timestamp += 960 * 6
        t_before = vc.timestamp
        clock[0] += 2.0
        m.add("b", Tone(1)); m.read()
        check("deuxième pause : repère renouvelé (2 s rattrapées, pas davantage)", vc.timestamp == t_before + 96000 - 5760, (vc.timestamp, t_before))
    finally:
        S.time.monotonic = real_monotonic

    print("\n--- Vrai AudioPlayer de discord.py : la chronologie RTP reste à l'heure après une pause ---")
    def player_timeline(sync):
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        speaking = []

        class Client:
            timeout = 5
            def __init__(self):
                self.timestamp = 0
                self.packets = []  # (instant, horodatage, charge utile)
                self.client = types.SimpleNamespace(loop=loop)
                self.ws = types.SimpleNamespace(speak=self._speak)
            async def _speak(self, state):
                speaking.append(state)
            def is_connected(self):
                return True
            def send_audio_packet(self, data, *, encode=True):
                self.packets.append((time.perf_counter(), self.timestamp, data))
                self.timestamp = (self.timestamp + 960) & 0xFFFFFFFF
            def pause(self):
                player.pause()
            def resume(self):
                player.resume()

        client = Client()
        mixer = S.MixingAudioSource(idle_sleep_frames=N)
        if not sync:
            mixer._sync_rtp_timestamp = lambda: None  # témoin : le comportement d'origine, sans rattrapage
        mixer.attach(client)
        player = S.discord.player.AudioPlayer(mixer, client)
        player.start()
        time.sleep(0.6)                       # silence : pause au bout de 200 ms
        time.sleep(1.5)                       # 1,5 s en pause
        mixer.add("a", Tone(5))
        time.sleep(0.5)
        player.stop()
        player.join(2)
        loop.call_soon_threadsafe(loop.stop)
        tone = [p for p in client.packets if p[2][:2] == b"\x01\x00"]
        return client, tone, speaking

    for sync, label in ((True, "avec rattrapage"), (False, "témoin sans rattrapage")):
        client, tone, speaking = player_timeline(sync)
        if not tone:
            check(f"{label} : le son a bien été émis", False, len(client.packets))
            continue
        first_tone = tone[0]
        # repère de la chronologie : la dernière trame PCM envoyée avant la salve de silence Opus que discord.py émet en pause
        first_opus_silence = next(i for i, p in enumerate(client.packets) if len(p[2]) < 100)
        marker = client.packets[first_opus_silence - 1]
        wall = first_tone[0] - marker[0]                            # secondes réelles écoulées entre cette trame et le son
        rtp = ((first_tone[1] - marker[1]) & 0xFFFFFFFF) / 48000.0  # secondes que le récepteur déduit de l'horodatage
        drift = wall - rtp
        if sync:
            check(f"{label} : horodatage RTP et horloge réelle concordent à 50 ms près (écart {drift * 1000:.0f} ms)", abs(drift) < 0.05, (wall, rtp))
            check(f"{label} : le voyant s'est éteint puis rallumé ({len(speaking)} changements d'état)", len(speaking) >= 3, speaking)
        else:
            check(f"{label} : le flux accuse bien ~1,5 s de retard (écart {drift * 1000:.0f} ms) : le test voit le défaut", drift > 1.0, (wall, rtp))

    print("\n--- Réglage voice_idle_sleep_s validé au démarrage ---")
    import wb_config
    bad_ok = True
    for bad in (-1, "abc", True, 7200, None):
        try:
            wb_config.validate_limits({"voice_idle_sleep_s": bad}); bad_ok = False
        except wb_config.ConfigError:
            pass
    check("valeurs invalides refusées (négatif, texte, booléen, > 3600 s, null)", bad_ok)
    try:
        wb_config.validate_limits({"voice_idle_sleep_s": 0}); wb_config.validate_limits({"voice_idle_sleep_s": 20.5})
        accepted = True
    except wb_config.ConfigError:
        accepted = False
    check("0 et 20,5 s acceptés", accepted)
    cfg = wb_config.load_config(environ={"WASEBOARD_VOICE_IDLE_SLEEP_S": "20"}, config_path=Path("inexistant-pour-le-test.json"))
    check("variable d'environnement WASEBOARD_VOICE_IDLE_SLEEP_S prise en compte", cfg.get("voice_idle_sleep_s") == 20.0, cfg)

    print("\n--- Échec de la pause ---")
    vc = FakeVC(fail_pause=True); m = S.MixingAudioSource(idle_sleep_frames=N); m.attach(vc)
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
