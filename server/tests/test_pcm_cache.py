"""Cache des sons décodés (PCMCache / PCMBufferSource) : un clic démarre sans attendre ffmpeg.
Vrai code de server.py et vrai ffmpeg (déjà requis par le serveur) ; aucun jeton ni Discord réel.
Dossier de données isolé (_env.py)."""
import asyncio
import math
import shutil
import struct
import sys
import time
import wave

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402
import wb_config  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


FRAME = S.FRAME_BYTES


def make_wav(path, seconds, rate=44100, channels=1):
    """Un la 440 Hz : mono 44,1 kHz, donc ffmpeg doit rééchantillonner en 48 kHz stéréo comme en production."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) * channels for i in range(n)))


def drain(source):
    """Lit une source jusqu'au bout, renvoie les octets (la dernière trame peut être partielle)."""
    out = bytearray()
    while True:
        data = source.read()
        if not data:
            return bytes(out)
        out += data


async def main():
    print("\n--- PCMCache : plafond, éviction, invalidation ---")
    c = S.PCMCache(1000)
    check("max_bytes = 0 : désactivé", not S.PCMCache(0).enabled and c.enabled)
    check("un son de 250 octets (le quart) est gardé", c.put(("a", 1, 1), b"x" * 250) and c.get(("a", 1, 1)) == b"x" * 250)
    check("un son de 251 octets (plus du quart) est refusé", not c.put(("big", 1, 1), b"x" * 251) and c.get(("big", 1, 1)) is None)
    check("un son refusé n'est plus jamais redécodé (begin_load = False)", not c.begin_load(("big", 1, 1)))
    c = S.PCMCache(1000)
    for i in range(4):
        c.put((f"s{i}", 1, 1), bytes([i]) * 250)
    c.get(("s0", 1, 1))                      # s0 redevient « récent »
    c.put(("s4", 1, 1), b"\x04" * 250)       # dépasse 1000 : évince le moins récent (s1)
    check("éviction : le moins récemment joué part en premier", c.get(("s1", 1, 1)) is None and c.get(("s0", 1, 1)) is not None
          and c.get(("s4", 1, 1)) is not None, c.used_bytes)
    check("la mémoire utilisée ne dépasse jamais le plafond", c.used_bytes <= 1000, c.used_bytes)
    check("clé différente (fichier remplacé) : aucune ancienne version servie", c.get(("s0", 2, 1)) is None)
    k = ("z", 9, 9)
    check("begin_load : une seule fois pour un même son", c.begin_load(k) and not c.begin_load(k))
    c.end_load(k, b"y" * 10)
    check("après chargement : en mémoire, plus à charger", c.get(k) == b"y" * 10 and not c.begin_load(k))
    k2 = ("fail", 1, 1)
    c.begin_load(k2); c.end_load(k2, None)
    check("échec de décodage : jamais réessayé en boucle", not c.begin_load(k2))

    print("\n--- decode_to_pcm / PCMBufferSource : mêmes octets que ffmpeg en flux ---")
    wav = _env.DATA_DIR / "tone.wav"
    make_wav(wav, 1.0)
    pcm = S.decode_to_pcm(str(wav), 10_000_000)
    check("1 s de son -> 48 000 échantillons stéréo 16 bits (192 000 octets, à une trame près)",
          pcm is not None and abs(len(pcm) - 192000) <= FRAME, None if pcm is None else len(pcm))
    check("trop gros pour la limite : None (lu en flux)", S.decode_to_pcm(str(wav), 1000) is None)
    check("fichier illisible : None", S.decode_to_pcm(str(_env.DATA_DIR / "absent.wav"), 10_000_000) is None)

    streamed = drain(S.discord.FFmpegPCMAudio(str(wav)))
    cached = drain(S.PCMBufferSource(pcm))
    check("sans découpe : exactement les mêmes octets que FFmpegPCMAudio", cached == streamed, (len(cached), len(streamed)))

    start, end = 250, 750
    before, options = S.trim_ffmpeg_options({"trim_start_ms": start, "trim_end_ms": end})
    streamed_trim = drain(S.discord.FFmpegPCMAudio(str(wav), before_options=before, options=options))
    cached_trim = drain(S.PCMBufferSource(pcm, start, end))
    check("découpe 0,25 s -> 0,75 s : 96 000 octets (même durée que ffmpeg à une trame près)",
          len(cached_trim) == 500 * S.PCM_BYTES_PER_MS and abs(len(cached_trim) - len(streamed_trim)) <= FRAME,
          (len(cached_trim), len(streamed_trim)))
    check("découpe : mêmes échantillons que ffmpeg (écart moyen < 1 % de la pleine échelle)",
          mean_gap(cached_trim, streamed_trim) < 330, mean_gap(cached_trim, streamed_trim))
    check("fin de flux : b'' répété sans erreur", S.PCMBufferSource(b"").read() == b"" and S.PCMBufferSource(pcm, 900, 100).read() == b"")

    print("\n--- _make_play_source : instantané en mémoire, ffmpeg sinon ---")
    bot = S.bot
    sound_id = "pcmtest"
    path = S.SOUNDS_DIR / f"{sound_id}.wav"
    shutil.copy(wav, path)
    entry = {"id": sound_id, "extension": ".wav", "name": "tone"}

    bot.pcm_cache = S.PCMCache(0)
    src, kind = bot._make_play_source(sound_id, entry, path, 1.0)
    check("cache désactivé : ffmpeg, rien n'est décodé en arrière-plan", kind == "ffmpeg" and bot.pcm_cache.used_bytes == 0)
    src.cleanup()

    bot.pcm_cache = S.PCMCache(64 * 1024 * 1024)
    src, kind = bot._make_play_source(sound_id, entry, path, 1.0)
    check("1re lecture (cache vide) : ffmpeg, comme avant", kind == "ffmpeg")
    t0 = time.perf_counter(); src.read(); ffmpeg_ms = (time.perf_counter() - t0) * 1000
    src.cleanup()
    for _ in range(100):                      # le décodage en arrière-plan se termine
        if bot.pcm_cache.used_bytes:
            break
        await asyncio.sleep(0.05)
    check("le son est décodé en arrière-plan après la 1re lecture", bot.pcm_cache.used_bytes > 0, bot.pcm_cache.used_bytes)

    src, kind = bot._make_play_source(sound_id, entry, path, 1.0)
    check("2e lecture : depuis la mémoire", kind == "mémoire")
    t0 = time.perf_counter(); first = src.read(); cache_ms = (time.perf_counter() - t0) * 1000
    check(f"1re trame lue en mémoire : instantanée ({cache_ms:.2f} ms, contre {ffmpeg_ms:.0f} ms avec ffmpeg)", cache_ms < 5 and cache_ms < ffmpeg_ms, (cache_ms, ffmpeg_ms))
    check("la trame fait bien 20 ms et n'est pas du silence", len(first) == FRAME and first != S.SILENCE)

    src_half, _ = bot._make_play_source(sound_id, entry, path, 0.5)
    half = src_half.read()
    check("le volume s'applique aussi à la source en mémoire (0,5 -> amplitude divisée par 2)",
          abs(peak(half) * 2 - peak(first)) <= 4, (peak(half), peak(first)))

    trimmed = dict(entry, trim_start_ms=250, trim_end_ms=750)
    src_t, kind_t = bot._make_play_source(sound_id, trimmed, path, 1.0)
    check("son découpé : joué depuis la mémoire, sur la portion gardée seulement",
          kind_t == "mémoire" and len(drain(src_t.original)) == 500 * S.PCM_BYTES_PER_MS)

    print("\n--- Fichier remplacé : l'ancienne version n'est jamais rejouée ---")
    make_wav(path, 2.0)
    import os
    os.utime(path, (time.time() + 5, time.time() + 5))
    src, kind = bot._make_play_source(sound_id, entry, path, 1.0)
    check("fichier modifié : relu depuis le disque (ffmpeg), pas depuis l'ancienne copie", kind == "ffmpeg")
    src.cleanup()

    print("\n--- Mixeur : un son en mémoire se mélange comme les autres ---")
    for _ in range(100):
        if bot.pcm_cache.get(bot._pcm_key(sound_id, path)):
            break
        await asyncio.sleep(0.05)
    src, kind = bot._make_play_source(sound_id, entry, path, 1.0)
    m = S.MixingAudioSource()
    m.add("a", src)
    frames = [m.read() for _ in range(5)]
    check("le mixeur lit des trames non nulles dès le premier appel", kind == "mémoire" and all(f != S.SILENCE and len(f) == FRAME for f in frames))

    print("\n--- Réglage pcm_cache_mb ---")
    bad_ok = True
    for bad in (-1, "abc", True, 9000, None):
        try:
            wb_config.validate_limits({"pcm_cache_mb": bad}); bad_ok = False
        except wb_config.ConfigError:
            pass
    check("valeurs invalides refusées (négatif, texte, booléen, > 8192, null)", bad_ok)
    wb_config.validate_limits({"pcm_cache_mb": 0}); wb_config.validate_limits({"pcm_cache_mb": 256})
    cfg = wb_config.load_config(environ={"WASEBOARD_PCM_CACHE_MB": "128"}, config_path=_env.DATA_DIR / "inexistant.json")
    check("variable d'environnement WASEBOARD_PCM_CACHE_MB prise en compte", cfg.get("pcm_cache_mb") == 128.0, cfg)
    check("par défaut : désactivé", S.PCM_CACHE_MB == 0)


def peak(frame):
    samples = struct.unpack(f"<{len(frame) // 2}h", frame)
    return max(abs(v) for v in samples)


def mean_gap(a, b):
    n = min(len(a), len(b)) // 2
    sa, sb = struct.unpack(f"<{n}h", a[:n * 2]), struct.unpack(f"<{n}h", b[:n * 2])
    return sum(abs(x - y) for x, y in zip(sa, sb)) / max(n, 1)


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
shutil.rmtree(S.SOUNDS_DIR, ignore_errors=True); S.SOUNDS_DIR.mkdir(); S.TRASH_DIR.mkdir()
asyncio.run(main())
print("\nRÉSULTAT :", "TOUT PASSE" if all(results) else f"{results.count(False)} échec(s) sur {len(results)}", f"({len(results)} vérifications)")
sys.exit(0 if all(results) else 1)
