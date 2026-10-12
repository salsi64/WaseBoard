"""Moteur audio (audio.py) : limiteur doux, boucle sans coupure, plafond de durée, arrêt d'un seul son, réduction
automatique (ducking) des sons d'ambiance, et fin de lecture de plusieurs sons de durées différentes.
Vrai code, fausses sources de trames : ni ffmpeg ni Discord. Dossier de données isolé (_env.py)."""
import sys

import numpy as np

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402
import audio as A  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


FRAME = A.FRAME_BYTES


def frame_of(value):
    return np.full(A.FRAME_SAMPLES, value, dtype=np.int16).tobytes()


def first_sample(frame):
    return int(np.frombuffer(frame, dtype=np.int16)[0])


class Const(A.discord.AudioSource):
    """`frames` trames d'un niveau constant (None = sans fin), puis fin de flux."""
    def __init__(self, value, frames=None):
        self.value, self.left, self.cleaned = value, frames, False

    def read(self):
        if self.left is not None:
            if self.left <= 0:
                return b""
            self.left -= 1
        return frame_of(self.value)

    def cleanup(self):
        self.cleaned = True


# ------------------------------------------------------------------ limiteur
print("\n--- Limiteur doux (soft_limit) ---")
rng = np.random.default_rng(7)
quiet = rng.integers(-27000, 27000, 5000).astype(np.float32)
check("identité tant que |x| <= 85 % de la pleine échelle (un mélange raisonnable n'est jamais modifié)",
      np.array_equal(A.soft_limit(quiet), quiet.astype(np.int16)))
big = np.array([30000, 40000, 65000, 200000, -40000, -200000], dtype=np.float32)
out = A.soft_limit(big)
check("ne dépasse jamais la pleine échelle, même pour 6 sons à fond", out.max() <= 32767 and out.min() >= -32768, out.tolist())
check("sortie en int16", out.dtype == np.int16)
check("symétrique (pas de décalage de signe)", np.array_equal(A.soft_limit(big), -A.soft_limit(-big)))
ramp = np.linspace(-150000, 150000, 4001, dtype=np.float32)
check("monotone : un signal plus fort ne sort jamais plus faible", np.all(np.diff(A.soft_limit(ramp).astype(np.int32)) >= 0))
knee = 0.85 * 32767
near = A.soft_limit(np.array([knee - 5, knee, knee + 5], dtype=np.float32)).astype(np.float32)
check("continu au genou : pas de saut audible", abs(near[1] - near[0]) < 8 and abs(near[2] - near[1]) < 8, near.tolist())
check("compression au-delà du genou : 40000 sort entre le genou et la pleine échelle", knee < A.soft_limit(np.array([40000], dtype=np.float32))[0] < 32767)

print("\n--- Limiteur dans le mixeur ---")
m = A.MixingAudioSource()
m.add("a", Const(20000)); m.add("b", Const(20000))
mixed = first_sample(m.read())
check("deux sons forts : somme comprimée en douceur (pas d'écrêtage à 32767 ni de débordement négatif)", 27852 < mixed < 32767, mixed)
m = A.MixingAudioSource()
m.add("a", Const(32767))
check("un son seul à pleine échelle : octets inchangés", m.read() == frame_of(32767))
m = A.MixingAudioSource()
m.add("a", Const(12000)); m.add("b", Const(9000))
check("mélange normal : somme exacte", first_sample(m.read()) == 21000)
m = A.MixingAudioSource()
m.add("a", Const(-30000)); m.add("b", Const(-30000)); m.add("c", Const(-30000))
check("trois sons négatifs à fond : borné aussi côté négatif", -32768 <= first_sample(m.read()) < -27852)

# ------------------------------------------------------------------ boucle
print("\n--- Boucle (PCMBufferSource loop) ---")
buf = bytes(range(256)) * 60                      # 15 360 octets = 4 trames exactement
src = A.PCMBufferSource(buf, loop=True)
data = b"".join(src.read() for _ in range(9))
check("9 trames lues sur un son de 4 trames : jamais de fin ni de silence", len(data) == 9 * FRAME and all(len(src.read()) == FRAME for _ in range(5)))
check("le son recommence à l'identique", data == (buf * 3)[:len(data)])
odd = bytes(range(256)) * 25                      # 6 400 octets : 1 trame + 2 560 octets
src = A.PCMBufferSource(odd, loop=True)
data = b"".join(src.read() for _ in range(7))
check("raccord sans trou quand la durée n'est pas un multiple de 20 ms (la fin est suivie directement du début)",
      data == (odd * 8)[:len(data)])
src = A.PCMBufferSource(buf, 20, 40, loop=True)   # portion de 20 ms à 40 ms = 3 840 octets
data = b"".join(src.read() for _ in range(3))
check("boucle sur la portion gardée d'un son découpé (3 trames identiques)", data[:FRAME] == data[FRAME:2 * FRAME] == data[2 * FRAME:] == buf[20 * A.PCM_BYTES_PER_MS:40 * A.PCM_BYTES_PER_MS])
src = A.PCMBufferSource(buf, loop=True, max_frames=6)
frames = [src.read() for _ in range(8)]
check("plafond de durée : s'arrête après max_frames trames (b'' ensuite)", all(frames[:6]) and frames[6] == b"" and frames[7] == b"")
src = A.PCMBufferSource(buf)
frames = [src.read() for _ in range(6)]
check("sans boucle : comportement inchangé (4 trames puis b'')", all(len(f) == FRAME for f in frames[:4]) and frames[4] == b"")
check("tampon vide : pas de boucle infinie, pas d'erreur", A.PCMBufferSource(b"", loop=True).read() == b"")

# ------------------------------------------------------------------ fin de lecture de plusieurs sons
print("\n--- Fins de lecture (plusieurs sons de durées différentes) ---")
finished = []
m = A.MixingAudioSource()
m.add("court", Const(1000, 1), on_finish=lambda: finished.append("court"))
m.add("moyen", Const(1000, 4), on_finish=lambda: finished.append("moyen"))
m.add("long", Const(1000, 8), on_finish=lambda: finished.append("long"))
for _ in range(3):
    m.read()
check("le son le plus court est retiré dès qu'il finit même s'il n'est pas le dernier de la liste (callback appelé une fois)",
      finished == ["court"] and m.count() == 2 and not m.has("court"), (finished, m.count()))
for _ in range(12):
    m.read()
check("chaque son est retiré à sa propre fin, dans l'ordre", finished == ["court", "moyen", "long"] and m.count() == 0, finished)

# ------------------------------------------------------------------ arrêt d'un son
print("\n--- Arrêt d'un seul son ---")
ended = []
a, b = Const(5000), Const(3000)
m = A.MixingAudioSource()
m.add("a", a, on_finish=lambda: ended.append("a")); m.add("b", b, on_finish=lambda: ended.append("b"), bed=True)
check("stop(a) : renvoie True, le son est nettoyé et son callback appelé", m.stop("a") is True and a.cleaned and ended == ["a"])
check("les autres sons continuent", m.has("b") and not m.has("a") and first_sample(m.read()) == 3000)
check("stop d'un son absent : False, rien ne bouge", m.stop("zzz") is False and m.has("b"))
check("stop retire aussi le marquage d'ambiance", "b" not in m._bed or (m.stop("b") and "b" not in m._bed))
m.add("x", Const(1), bed=True); m.add("x", Const(1), bed=False)
check("rejouer un son comme son normal retire son marquage d'ambiance", "x" not in m._bed)

# ------------------------------------------------------------------ ducking
print("\n--- Sons d'ambiance : réduction automatique ---")
m = A.MixingAudioSource(duck_gain=0.3)
m.add("pluie", Const(10000), bed=True)
check("l'ambiance seule joue à son niveau normal", all(first_sample(m.read()) == 10000 for _ in range(5)))
m.add("bruit", Const(5000, 10))
levels = [first_sample(m.read()) for _ in range(10)]
bed_levels = [v - 5000 for v in levels]
check("un autre son arrive : l'ambiance descend en 2 trames (40 ms)", bed_levels[0] == 6500 and bed_levels[1] == 3000, bed_levels[:3])
check("puis reste réduite tant que l'autre son joue", all(v == 3000 for v in bed_levels[1:]), bed_levels)
after = [first_sample(m.read()) for _ in range(30)]
check("à la fin de l'autre son, l'ambiance remonte progressivement (jamais de saut)", after[0] > 3000 and after[0] < 4000 and after == sorted(after), after[:5])
check("et retrouve exactement son niveau normal au bout de 500 ms", after[-1] == 10000 and after[24] == 10000, (after[23], after[24]))
m = A.MixingAudioSource(duck_gain=0.3)
m.add("pluie", Const(10000), bed=True); m.add("vent", Const(8000), bed=True)
check("deux ambiances ensemble : aucune réduction (rien ne les couvre)", first_sample(m.read()) == 18000)
m = A.MixingAudioSource(duck_gain=0.3)
m.add("a", Const(6000)); m.add("b", Const(6000))
check("deux sons normaux : aucune réduction", first_sample(m.read()) == 12000)
m = A.MixingAudioSource(duck_gain=1.0)
m.add("pluie", Const(10000), bed=True); m.add("bruit", Const(5000))
check("duck_gain = 1 : réduction désactivée", all(first_sample(m.read()) == 15000 for _ in range(4)))
m = A.MixingAudioSource(duck_gain=0.0)
m.add("pluie", Const(10000), bed=True); m.add("bruit", Const(5000))
for _ in range(3):
    m.read()
check("duck_gain = 0 : l'ambiance se tait complètement pendant l'autre son", first_sample(m.read()) == 5000)
check("réglage par défaut : 0,3 (lu depuis la configuration)", S.DUCK_GAIN == 0.3 and A.DUCK_GAIN == S.DUCK_GAIN, (S.DUCK_GAIN, A.DUCK_GAIN))

print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
