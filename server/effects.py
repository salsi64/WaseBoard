"""Effets de lecture (hauteur, vitesse, écho, basses) : validation stricte de ce que demande le client et construction de
la chaîne de filtres ffmpeg (-af).

Rien de ce qui vient du client n'est jamais inséré tel quel dans la commande : seules des valeurs numériques bornées,
re-formatées ici, entrent dans la chaîne ; toute clé inconnue est refusée.
"""
import math
from typing import Optional

SAMPLE_RATE = 48000

PITCH_RANGE = (-12.0, 12.0)   # demi-tons (une octave de chaque côté)
SPEED_RANGE = (0.5, 2.0)      # facteur de vitesse, sans changer la hauteur
ECHO_RANGE = (0.0, 1.0)       # intensité de l'écho
BASS_RANGE = (0.0, 15.0)      # renforcement des basses, en dB

EFFECT_NAMES = ("pitch", "speed", "echo", "bass")


def _number(name: str, value, bounds: tuple[float, float]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Effet « {name} » : un nombre est attendu.")
    low, high = bounds
    if not low <= value <= high:
        raise ValueError(f"Effet « {name} » : la valeur doit être comprise entre {low:g} et {high:g}.")
    return round(float(value), 2)


def parse_effects(raw) -> Optional[dict]:
    """Valide les effets demandés (objet JSON) et renvoie les seuls effets réellement actifs, normalisés — ou None si
    rien n'est à appliquer (aucun effet, ou toutes les valeurs neutres). Lève ValueError, avec un message lisible,
    pour un effet inconnu ou une valeur invalide."""
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict):
        raise ValueError("« effects » doit être un objet, par exemple {\"pitch\": 3, \"echo\": true}.")

    effects: dict = {}
    for name, value in raw.items():
        if name == "pitch":
            number = _number(name, value, PITCH_RANGE)
            if number != 0:
                effects[name] = number
        elif name == "speed":
            number = _number(name, value, SPEED_RANGE)
            if number != 1:
                effects[name] = number
        elif name == "bass":
            number = _number(name, value, BASS_RANGE)
            if number > 0:
                effects[name] = number
        elif name == "echo":
            if value is True:
                value = 0.5
            if value is False or value is None:
                continue
            number = _number(name, value, ECHO_RANGE)
            if number > 0:
                effects[name] = number
        else:
            raise ValueError(f"Effet inconnu : « {name} » (possibles : {', '.join(EFFECT_NAMES)}).")
    return effects or None


def build_audio_filter(effects: Optional[dict]) -> Optional[str]:
    """Chaîne de filtres ffmpeg (-af) pour des effets déjà validés par parse_effects ; None s'il n'y en a pas.

    Ordre : hauteur, vitesse, basses, écho. La hauteur change la fréquence d'échantillonnage puis la compense par la
    vitesse (atempo, valable de 0,5 à 2) : la durée du son ne change pas ; la vitesse change la durée sans changer la
    hauteur."""
    if not effects:
        return None
    chain = []

    pitch = effects.get("pitch")
    if pitch:
        ratio = 2 ** (pitch / 12)
        chain.append(f"aresample={SAMPLE_RATE},asetrate={round(SAMPLE_RATE * ratio)},aresample={SAMPLE_RATE},atempo={1 / ratio:.6f}")

    speed = effects.get("speed")
    if speed:
        chain.append(f"atempo={speed:.2f}")

    bass = effects.get("bass")
    if bass:
        chain.append(f"bass=g={bass:g}")

    echo = effects.get("echo")
    if echo:
        chain.append(f"aecho=0.8:0.88:90:{0.15 + 0.5 * echo:.2f}")

    return ",".join(chain) or None
