"""Audio du serveur : décodage des sons, cache de sons décodés en mémoire et mixeur persistant d'un salon vocal.

Extrait de server.py sans changement de comportement. Ce module ne lit aucune configuration : server.py règle
`VOICE_IDLE_SLEEP_S` (réglage `voice_idle_sleep_s`) après avoir chargé config.json.
"""
import logging
import os
import subprocess
import threading
import time
from collections import OrderedDict
from typing import Optional

import discord
import numpy as np

log = logging.getLogger("waseboard-server")

# Format attendu par discord.py pour une frame audio : 20ms de PCM stéréo 16 bits @ 48kHz.
FRAME_BYTES = 3840
SILENCE = b"\x00" * FRAME_BYTES
RTP_CLOCK_HZ = 48000  # horloge RTP de l'audio Discord (Opus 48 kHz) : l'horodatage d'un paquet avance de 960 par trame de 20 ms

#: Secondes sans aucun son avant que le bot cesse d'émettre (0 = flux continu). Réglé par server.py depuis la config.
VOICE_IDLE_SLEEP_S: float = 0

PCM_BYTES_PER_MS = RTP_CLOCK_HZ * 2 * 2 // 1000  # s16le, 48 kHz, stéréo : 192 octets par milliseconde


def decode_to_pcm(path: str, limit_bytes: int) -> Optional[bytes]:
    """Décode tout un fichier audio en PCM s16le 48 kHz stéréo (exactement ce que produit FFmpegPCMAudio), avec la
    priorité la plus basse. None si le son dépasse `limit_bytes` une fois décodé ou si ffmpeg échoue : l'appelant le
    lira alors en flux, comme avant. Bloquant : à appeler depuis un fil, jamais depuis la boucle d'évènements."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None)
    try:
        data = proc.stdout.read(limit_bytes + 1)
        if len(data) > limit_bytes or not data:
            return None
        return data if proc.wait(timeout=30) == 0 else None
    except Exception:
        return None
    finally:
        proc.kill()
        proc.wait()
        proc.stdout.close()


class PCMCache:
    """Sons décodés gardés en mémoire (au plus `max_bytes`, les moins récemment joués étant évincés en premier).
    Clé = (id du son, date de modification, taille) : remplacer le fichier d'un son change la clé, l'ancienne version
    n'est donc jamais rejouée. Un son trop long pour tenir (plus du quart du cache) n'est pas gardé et reste lu en
    flux. Sûr à utiliser depuis plusieurs fils."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self._items: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._size = 0
        self._loading: set = set()
        self._uncacheable: set = set()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    @property
    def max_item_bytes(self) -> int:
        return self.max_bytes // 4

    @property
    def used_bytes(self) -> int:
        return self._size

    def get(self, key: tuple) -> Optional[bytes]:
        with self._lock:
            data = self._items.get(key)
            if data is not None:
                self._items.move_to_end(key)
            return data

    def put(self, key: tuple, data: bytes) -> bool:
        with self._lock:
            if len(data) > self.max_item_bytes:
                self._uncacheable.add(key)
                return False
            previous = self._items.pop(key, None)
            if previous is not None:
                self._size -= len(previous)
            self._items[key] = data
            self._size += len(data)
            while self._size > self.max_bytes and len(self._items) > 1:
                _, evicted = self._items.popitem(last=False)
                self._size -= len(evicted)
            return True

    def begin_load(self, key: tuple) -> bool:
        """True si ce son doit être décodé maintenant (ni en mémoire, ni déjà en cours, ni connu comme trop gros)."""
        with self._lock:
            if not self.enabled or key in self._items or key in self._loading or key in self._uncacheable:
                return False
            self._loading.add(key)
            return True

    def end_load(self, key: tuple, data: Optional[bytes]) -> None:
        if data is None:
            with self._lock:
                self._uncacheable.add(key)
        else:
            self.put(key, data)
        with self._lock:
            self._loading.discard(key)
            if len(self._uncacheable) > 2000:  # garde-fou : l'ensemble ne doit pas grossir sans fin
                self._uncacheable.clear()


class PCMBufferSource(discord.AudioSource):
    """Lit un son déjà décodé (voir PCMCache) par trames de 20 ms, sans aucun processus ni attente : le premier échantillon
    part immédiatement. `start_ms`/`end_ms` = la portion gardée d'un son découpé (le tampon contient le fichier entier)."""

    def __init__(self, buffer: bytes, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> None:
        begin = 0 if start_ms is None else min(len(buffer), start_ms * PCM_BYTES_PER_MS // 4 * 4)
        end = len(buffer) if end_ms is None else min(len(buffer), end_ms * PCM_BYTES_PER_MS // 4 * 4)
        self._view = memoryview(buffer)[begin:max(begin, end)]
        self._pos = 0

    def read(self) -> bytes:
        chunk = self._view[self._pos:self._pos + FRAME_BYTES]
        self._pos += FRAME_BYTES
        return bytes(chunk)

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._view = memoryview(b"")


class MixingAudioSource(discord.AudioSource):
    """
    Source audio persistante d'un salon : mixe plusieurs sons différents (ajoutés via add(),
    indexés par sound_id) à chaque frame de 20ms au lieu de se remplacer. Rejouer le MÊME son
    coupe et relance l'instance précédente plutôt que de la superposer. Renvoie du silence
    quand rien ne joue (le voice_client ne doit jamais voir de fin de flux).

    Le callback "on_finish" optionnel se déclenche à la fin réelle du son (ou à son remplacement),
    utilisé pour effacer le highlight partagé au bon moment.

    Voyant « en train de parler » (facultatif, voice_idle_sleep_s) : par défaut le lecteur ne s'arrête jamais — le
    flux reste continu et le premier son part sans la moindre latence, mais le voyant du bot reste allumé. Si
    voice_idle_sleep_s > 0, le lecteur est mis en PAUSE (le voyant s'éteint, plus aucun paquet audio n'est envoyé) après
    ce délai sans son, et relancé par add() dès qu'un son arrive — voir attach().

    Piège de la pause : discord.py n'avance pas l'horodatage RTP pendant qu'il se tait. Au réveil, le récepteur voit
    donc des paquets « en retard » de toute la durée de la pause et les rejette/retarde (latence au premier son, c'est
    ce qui rendait chaque clic lent). On avance l'horodatage de la durée écoulée au réveil, comme le fait tout émetteur
    RTP après un silence (voir _sync_rtp_timestamp).
    """

    def __init__(self, idle_sleep_frames: Optional[int] = None) -> None:
        self._entries: dict[str, tuple[discord.AudioSource, Optional[callable]]] = {}
        self._lock = threading.Lock()
        self._voice_client = None
        # Trames de 20 ms sans aucun son avant la pause ; 0 = jamais de pause.
        self._sleep_after = round(VOICE_IDLE_SLEEP_S * 50) if idle_sleep_frames is None else idle_sleep_frames
        self._idle_frames = 0
        self._asleep = False
        self._slept_at = 0.0
        self._slept_ts: Optional[int] = None
        self._sync_pending = False

    def attach(self, voice_client) -> None:
        """Lie ce mixeur au client vocal qui le lit : permet de mettre le lecteur en pause quand rien ne joue."""
        self._voice_client = voice_client

    def _wake_locked(self) -> None:
        """Un son arrive : sort le lecteur de sa pause (rallume le voyant). Appelé verrou pris, comme la mise en pause,
        pour qu'un add() ne puisse jamais s'intercaler entre « plus rien » et « pause » et laisser un son bloqué."""
        self._idle_frames = 0
        if self._asleep:
            self._asleep = False
            self._sync_pending = True  # horodatage RTP à rattraper juste avant le premier paquet (voir read())
            if self._voice_client is not None:
                try:
                    self._voice_client.resume()
                except Exception:
                    log.exception("Reprise du lecteur vocal impossible")

    def _maybe_sleep_locked(self) -> None:
        if self._sleep_after <= 0 or self._asleep or self._idle_frames < self._sleep_after or self._voice_client is None:
            return
        self._asleep = True
        # Repère de l'horloge au moment de la pause : appelé depuis read(), donc depuis le fil du lecteur, juste avant
        # l'envoi de la trame que lit ce read() — vc.timestamp est exactement l'horodatage de ce paquet.
        self._slept_at = time.monotonic()
        self._slept_ts = getattr(self._voice_client, "timestamp", None)
        try:
            self._voice_client.pause()
        except Exception:
            self._asleep = False
            self._idle_frames = 0  # on réessaiera dans 200 ms plutôt qu'à chaque frame
            log.exception("Pause du lecteur vocal impossible")

    def add(self, key: str, source: discord.AudioSource, on_finish: Optional[callable] = None) -> None:
        with self._lock:
            previous = self._entries.get(key)
            self._entries[key] = (source, on_finish)
            self._wake_locked()
        # Coupe l'ancienne instance après avoir publié la nouvelle (jamais de trou pour cette
        # clé), hors du verrou (cleanup/on_finish peuvent être lents).
        if previous is not None:
            old_source, old_on_finish = previous
            try:
                old_source.cleanup()
            except Exception:
                pass
            if old_on_finish is not None:
                try:
                    old_on_finish()
                except Exception:
                    log.exception("Erreur dans un callback on_finish (relance)")

    def count(self) -> int:
        """Nombre de sons actuellement en lecture sur ce mixeur (un processus ffmpeg chacun)."""
        with self._lock:
            return len(self._entries)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._entries

    def clear(self) -> None:
        """Coupe immédiatement tous les sons en cours sur ce mixeur (bouton "Stop tout")."""
        with self._lock:
            entries, self._entries = self._entries, {}
        self._finish_entries(entries.values())

    def read(self) -> bytes:
        frame = self._read_frame()
        with self._lock:
            sync, self._sync_pending = self._sync_pending, False
        if sync:
            # Après la lecture (qui peut avoir bloqué le temps que ffmpeg sorte sa première trame) et juste avant l'envoi
            # du paquet par le lecteur : l'horodatage doit refléter l'instant réel d'émission.
            self._sync_rtp_timestamp()
        return frame

    def _sync_rtp_timestamp(self) -> None:
        """Au réveil d'une pause : avance l'horodatage RTP du client vocal de la durée réellement écoulée (moins ce
        que les paquets de la pause ont déjà fait avancer), pour que le flux reprenne à l'heure — voir la docstring.
        Appelé depuis le fil du lecteur, entre deux paquets : rien d'autre n'écrit l'horodatage à cet instant."""
        vc, start_ts = self._voice_client, self._slept_ts
        if vc is None or start_ts is None:
            return
        try:
            elapsed = int((time.monotonic() - self._slept_at) * RTP_CLOCK_HZ)
            already = (vc.timestamp - start_ts) & 0xFFFFFFFF
            gap = elapsed - already
            if gap > 0:
                vc.timestamp = (vc.timestamp + gap) & 0xFFFFFFFF
        except Exception:
            log.exception("Resynchronisation de l'horodatage RTP impossible")

    def _read_frame(self) -> bytes:
        with self._lock:
            snapshot = list(self._entries.items())
            if snapshot:
                self._idle_frames = 0
            else:
                self._idle_frames += 1
                self._maybe_sleep_locked()

        if not snapshot:
            return SILENCE

        frames, finished = [], []
        for key, (source, _) in snapshot:
            try:
                data = source.read()
            except Exception:
                data = b""
            if data:
                frames.append(data)
            else:
                finished.append(key)

        if finished:
            finished_entries = []
            with self._lock:
                for key in finished:
                    current = self._entries.get(key)
                    # Ne retire QUE si c'est toujours la même instance qu'au moment du snapshot :
                    # entre-temps, un nouvel appui a pu déjà remplacer cette clé par une relance
                    # (voir add()) — dans ce cas, il ne faut surtout pas effacer la nouvelle entrée.
                    if current is not None and current[0] is source:
                        del self._entries[key]
                        finished_entries.append(current)
            self._finish_entries(finished_entries)

        if not frames:
            return SILENCE

        if len(frames) == 1:
            data = frames[0]
            return data if len(data) == FRAME_BYTES else data.ljust(FRAME_BYTES, b"\x00")

        # Saturation appliquée après chaque son ajouté (pas une somme brute clampée une seule
        # fois à la fin) : reproduit exactement le comportement de l'ancienne boucle scalaire,
        # où l'ordre d'empilement des sons peut influencer un résultat déjà saturé.
        mixed = np.zeros(1920, dtype=np.int32)
        for data in frames:
            if len(data) < FRAME_BYTES:
                data = data.ljust(FRAME_BYTES, b"\x00")
            samples = np.frombuffer(data[:FRAME_BYTES], dtype=np.int16).astype(np.int32)
            mixed = np.clip(mixed + samples, -32768, 32767)

        return mixed.astype(np.int16).tobytes()

    @staticmethod
    def _finish_entries(entries) -> None:
        for source, on_finish in entries:
            try:
                source.cleanup()
            except Exception:
                pass
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:
                    log.exception("Erreur dans un callback on_finish")

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        with self._lock:
            entries, self._entries = self._entries, {}
        self._finish_entries(entries.values())
