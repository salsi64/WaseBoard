"""Effets de lecture (effects.py) : validation stricte, chaîne de filtres ffmpeg sûre, et effet réel sur un vrai son
(durée et hauteur mesurées avec ffmpeg). Dossier de données isolé (_env.py)."""
import math
import random
import re
import shlex
import struct
import sys
import wave

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import audio as A  # noqa: E402
import effects as E  # noqa: E402
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def bad(raw):
    try:
        E.parse_effects(raw)
    except ValueError as ex:
        return str(ex)
    return None


print("\n--- parse_effects ---")
check("rien demandé -> None", E.parse_effects(None) is None and E.parse_effects({}) is None)
check("valeurs neutres -> None (aucun filtre inutile)", E.parse_effects({"pitch": 0, "speed": 1, "bass": 0, "echo": False}) is None)
check("valeurs valides normalisées (2 décimales)", E.parse_effects({"pitch": 3.14159, "speed": 1.5, "bass": 6}) == {"pitch": 3.14, "speed": 1.5, "bass": 6.0})
check("echo: true = intensité moyenne", E.parse_effects({"echo": True}) == {"echo": 0.5})
check("echo numérique accepté", E.parse_effects({"echo": 0.8}) == {"echo": 0.8})
check("bornes incluses acceptées", E.parse_effects({"pitch": -12, "speed": 2.0}) == {"pitch": -12.0, "speed": 2.0})
for label, raw in (("effet inconnu", {"reverb": 1}), ("hauteur trop haute", {"pitch": 13}), ("hauteur trop basse", {"pitch": -12.5}),
                   ("vitesse trop lente", {"speed": 0.4}), ("vitesse trop rapide", {"speed": 2.1}), ("basses trop fortes", {"bass": 16}),
                   ("basses négatives", {"bass": -1}), ("écho > 1", {"echo": 1.5}), ("texte à la place d'un nombre", {"pitch": "3"}),
                   ("booléen pour un nombre", {"pitch": True}), ("NaN", {"speed": float("nan")}), ("infini", {"speed": float("inf")}),
                   ("pas un objet", [1, 2]), ("pas un objet (texte)", "pitch=3")):
    check(f"refusé : {label}", bad(raw) is not None, raw)
check("message d'erreur lisible (nom de l'effet et bornes)", "pitch" in (bad({"pitch": 99}) or "") and "12" in (bad({"pitch": 99}) or ""))
check("effet inconnu : la liste des effets possibles est donnée", "pitch" in (bad({"x": 1}) or "") and "echo" in (bad({"x": 1}) or ""))

print("\n--- build_audio_filter ---")
check("pas d'effet -> None", E.build_audio_filter(None) is None and E.build_audio_filter({}) is None)
up = E.build_audio_filter({"pitch": 12})
check("+12 demi-tons : fréquence doublée, durée conservée (atempo 0,5)", "asetrate=96000" in up and "atempo=0.500000" in up, up)
down = E.build_audio_filter({"pitch": -12})
check("-12 demi-tons : fréquence divisée par 2 (atempo 2)", "asetrate=24000" in down and "atempo=2.000000" in down, down)
check("vitesse", E.build_audio_filter({"speed": 1.5}) == "atempo=1.50")
check("basses", E.build_audio_filter({"bass": 6}) == "bass=g=6")
check("écho (intensité 1)", E.build_audio_filter({"echo": 1.0}) == "aecho=0.8:0.88:90:0.65")
combo = E.build_audio_filter({"echo": 0.5, "bass": 4, "speed": 1.25, "pitch": 2})
parts = combo.split(",")
check("ordre : hauteur, vitesse, basses, écho", combo.index("asetrate") < combo.index("atempo=1.25") < combo.index("bass=") < combo.index("aecho"), combo)
safe = re.compile(r"^[a-z0-9=:,.\-]+$")
rng = random.Random(3)
ok = True
for _ in range(500):
    raw = {k: rng.choice([True, False, rng.uniform(-20, 20), rng.uniform(0, 3), rng.randint(-5, 5)]) for k in rng.sample(E.EFFECT_NAMES, rng.randint(1, 4))}
    try:
        flt = E.build_audio_filter(E.parse_effects(raw))
    except ValueError:
        continue
    if flt is not None and not safe.match(flt):
        ok = False
check("jamais de caractère inattendu dans la chaîne ffmpeg (500 entrées aléatoires)", ok)
entry = {"trim_start_ms": 250, "trim_end_ms": 750}
before, options = S.WaseBoardServer._effect_stream_options(entry, "atempo=1.50")
check("découpe à l'ENTRÉE (-ss/-t avant -i) pour que les filtres agissent sur la bonne portion", before == "-ss 0.250 -t 0.500", before)
check("l'option -af survit au découpage de la ligne de commande (shlex, comme discord.py)", shlex.split(options) == ["-af", "atempo=1.50"], options)
check("son non découpé : pas d'option d'entrée", S.WaseBoardServer._effect_stream_options({}, "bass=g=5")[0] is None)

print("\n--- Effets sur un vrai son (ffmpeg) ---")
wav = _env.DATA_DIR / "la.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(48000)))  # 1 s de la 440 Hz

BYTES_PER_S = 48000 * 4


def decode(**kw):
    return A.decode_to_pcm(str(wav), 50_000_000, **kw)


def seconds(pcm):
    return len(pcm) / BYTES_PER_S


def frequency(pcm):
    """Fréquence dominante (passages par zéro du canal gauche, croissants)."""
    left = struct.unpack(f"<{len(pcm) // 2}h", pcm)[::2]
    crossings = sum(1 for a, b in zip(left, left[1:]) if a <= 0 < b)
    return crossings / seconds(pcm)


dry = decode()
check("son sec : 1 s, 440 Hz", abs(seconds(dry) - 1.0) < 0.05 and abs(frequency(dry) - 440) < 15, (seconds(dry), frequency(dry)))
fast = decode(audio_filter=E.build_audio_filter({"speed": 2.0}))
check("vitesse x2 : durée divisée par 2, hauteur inchangée", abs(seconds(fast) - 0.5) < 0.06 and abs(frequency(fast) - 440) < 25, (seconds(fast), frequency(fast)))
slow = decode(audio_filter=E.build_audio_filter({"speed": 0.5}))
check("vitesse x0,5 : durée doublée, hauteur inchangée", abs(seconds(slow) - 2.0) < 0.1 and abs(frequency(slow) - 440) < 25, (seconds(slow), frequency(slow)))
high = decode(audio_filter=E.build_audio_filter({"pitch": 12}))
check("+12 demi-tons : une octave plus haut (880 Hz), même durée", abs(frequency(high) - 880) < 40 and abs(seconds(high) - 1.0) < 0.08, (seconds(high), frequency(high)))
low = decode(audio_filter=E.build_audio_filter({"pitch": -12}))
check("-12 demi-tons : une octave plus bas (220 Hz), même durée", abs(frequency(low) - 220) < 15 and abs(seconds(low) - 1.0) < 0.08, (seconds(low), frequency(low)))
echo = decode(audio_filter=E.build_audio_filter({"echo": 1.0}))
check("écho : le son change réellement (pas identique au son sec) et se prolonge de la queue de l'écho (~90 ms)", echo != dry and 1.0 <= seconds(echo) < 1.2, seconds(echo))
bass = decode(audio_filter=E.build_audio_filter({"bass": 12}))
check("basses : le son change, durée conservée", bass != dry and abs(seconds(bass) - 1.0) < 0.05)
trimmed = decode(start_ms=250, end_ms=750, audio_filter=E.build_audio_filter({"speed": 2.0}))
check("portion gardée (0,5 s) puis vitesse x2 : 0,25 s", abs(seconds(trimmed) - 0.25) < 0.05, seconds(trimmed))
check("découpe seule, sans effet : 0,5 s (comportement de decode_to_pcm étendu, jamais cassé)", abs(seconds(decode(start_ms=250, end_ms=750)) - 0.5) < 0.03)
check("filtre refusé par ffmpeg : None (jamais d'exception)", A.decode_to_pcm(str(wav), 50_000_000, audio_filter="nimportequoi=1") is None)

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
