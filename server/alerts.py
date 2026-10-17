"""Alertes d'exploitation : le bot prévient la personne qui héberge le serveur (message privé Discord) quand quelque chose
déraille — disque presque plein, certificat HTTPS qui va expirer, rafale d'erreurs, connexion à Discord coupée un
moment, redémarrage inattendu. Ce module ne contient que la logique (seuils, anti-répétition, lecture du certificat) ;
l'envoi du message et la tâche périodique sont dans server.py.

Ce qui ne peut PAS prévenir : un serveur complètement tombé. C'est le rôle du script indépendant `deploy/watchdog.sh`
(timer systemd, message sur un webhook Discord).
"""

import json
import logging
import os
import shutil
import socket
import ssl
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger("waseboard-alerts")

#: Délai minimal (secondes) entre deux messages du même type : un problème qui dure ne remplit pas la messagerie.
COOLDOWNS = {"disk": 12 * 3600, "cert": 24 * 3600, "errors": 30 * 60, "reconnect": 5 * 60, "restart": 10 * 60}

#: Code d'erreur OpenSSL « certificat expiré » (X509_V_ERR_CERT_HAS_EXPIRED).
EXPIRED_VERIFY_CODE = 10

#: Après un envoi raté (messages privés fermés, Discord injoignable), on réessaie au bout de ce délai plutôt qu'à chaque tour.
RETRY_AFTER_FAILURE = 10 * 60


class AlertState:
    """Mémoire des alertes, dans un petit fichier JSON : dernier envoi par type (pour ne pas répéter un message à chaque
    redémarrage) et si la dernière exécution du serveur s'est arrêtée proprement."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_sent: dict[str, float] = {}
        self.clean_shutdown: Optional[bool] = None  # None : premier démarrage connu
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sent = data.get("last_sent", {})
            self.last_sent = {k: float(v) for k, v in sent.items() if isinstance(v, (int, float))} if isinstance(sent, dict) else {}
            clean = data.get("clean_shutdown")
            self.clean_shutdown = clean if isinstance(clean, bool) else None
        except (OSError, ValueError, AttributeError):
            self.last_sent, self.clean_shutdown = {}, None  # fichier absent ou abîmé : on repart de zéro

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"last_sent": self.last_sent, "clean_shutdown": self.clean_shutdown}, f)
            os.replace(tmp, self.path)
        except OSError:
            log.warning("Impossible d'écrire %s : les alertes pourraient se répéter après un redémarrage.", self.path)

    def due(self, kind: str, now: float) -> bool:
        return now - self.last_sent.get(kind, 0.0) >= COOLDOWNS.get(kind, 0)


class AlertManager:
    """Envoie une alerte si son délai d'attente est écoulé. `send` : coroutine qui remet le texte à la personne (True si remis)."""

    def __init__(self, state: AlertState, send: Callable[[str], Awaitable[bool]], clock: Callable[[], float] = time.time) -> None:
        self.state, self._send, self._clock = state, send, clock

    async def notify(self, kind: str, text: str) -> bool:
        now = self._clock()
        if not self.state.due(kind, now):
            return False
        try:
            delivered = await self._send(text)
        except Exception:
            log.warning("Envoi de l'alerte « %s » impossible.", kind, exc_info=True)
            delivered = False
        # Remis : le délai normal repart. Raté : nouvel essai dans RETRY_AFTER_FAILURE seulement.
        self.state.last_sent[kind] = now if delivered else now - COOLDOWNS.get(kind, 0) + RETRY_AFTER_FAILURE
        self.state.save()
        return delivered


class ErrorBurstDetector(logging.Handler):
    """Compte les erreurs journalisées (niveau ERROR et plus) : `burst()` signale une rafale quand il y en a au moins
    `threshold` dans les `window_s` dernières secondes. threshold = 0 : désactivé. Les messages de ce module n'y comptent pas."""

    def __init__(self, threshold: int = 20, window_s: float = 300, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(level=logging.ERROR)
        self.threshold, self.window_s, self._clock = threshold, window_s, clock
        self._events: deque = deque()

    def emit(self, record: logging.LogRecord) -> None:
        if self.threshold <= 0 or record.name == log.name:
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        self._events.append((self._clock(), f"{record.name}: {message}"))
        while len(self._events) > max(self.threshold * 4, 100):
            self._events.popleft()

    def burst(self) -> Optional[tuple[int, str]]:
        """(nombre d'erreurs dans la fenêtre, dernier message) s'il y a rafale, sinon None."""
        if self.threshold <= 0:
            return None
        limit = self._clock() - self.window_s
        while self._events and self._events[0][0] < limit:
            self._events.popleft()
        if len(self._events) >= self.threshold:
            return len(self._events), self._events[-1][1]
        return None


def disk_alert(path: Path, threshold_percent: float) -> Optional[str]:
    """Message si le disque qui contient `path` est rempli à `threshold_percent` % ou plus (0 = vérification désactivée)."""
    if threshold_percent <= 0:
        return None
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent = 100 * usage.used / usage.total if usage.total else 0
    if percent < threshold_percent:
        return None
    return (f"💾 **Disque presque plein** : {percent:.0f} % utilisé ({usage.free / 1024 ** 3:.1f} Go libres). "
            "Pensez à vider la corbeille de WaseBoard ou à nettoyer les anciennes sauvegardes.")


def read_certificate_expiry(host: str, port: int = 443, timeout: float = 6.0) -> Optional[float]:
    """Date d'expiration (secondes epoch) du certificat HTTPS servi pour `host`, ou None si on n'a pas pu le lire.
    On se connecte d'abord à la machine locale (127.0.0.1) en demandant le nom `host` : le serveur web de la machine
    répond avec le vrai certificat de ce nom, sans dépendre de la résolution DNS publique ni d'un retour du routeur vers
    lui-même ; à défaut (reverse proxy dans un autre conteneur), on essaie l'adresse publique. Lève ssl.SSLCertVerificationError
    UNIQUEMENT si le certificat est expiré : toute autre anomalie (un autre service sur le port 443, un nom qui ne
    correspond pas) est ignorée plutôt que signalée à tort."""
    context = ssl.create_default_context()
    last_error: Optional[Exception] = None
    for target in ("127.0.0.1", host):
        try:
            with socket.create_connection((target, port), timeout=timeout) as sock:
                with context.wrap_socket(sock, server_hostname=host) as tls:
                    return ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
        except ssl.SSLCertVerificationError as ex:
            if ex.verify_code == EXPIRED_VERIFY_CODE:
                raise
            last_error = ex
        except (OSError, KeyError, ValueError) as ex:
            last_error = ex
    log.debug("Certificat de %s illisible : %s", host, last_error)
    return None


def certificate_alert(public_url: str, min_days: float, now: Optional[float] = None,
                      reader: Callable[..., Optional[float]] = read_certificate_expiry) -> Optional[str]:
    """Message si le certificat HTTPS de `public_url` expire dans moins de `min_days` jours, ou n'est plus valable.
    Sans adresse https, sans lecture possible, ou min_days = 0 : None (on ne signale jamais ce qu'on ne peut pas vérifier)."""
    if min_days <= 0 or not public_url:
        return None
    parsed = urlparse(public_url)
    host = parsed.hostname
    if parsed.scheme != "https" or not host:
        return None
    try:
        expires = reader(host, parsed.port or 443)
    except ssl.SSLCertVerificationError:
        return f"🔒 **Certificat HTTPS expiré** pour {host} : les utilisateurs ne peuvent plus se connecter. Renouvelez-le (certbot renew)."
    if expires is None:
        return None
    days_left = (expires - (time.time() if now is None else now)) / 86400
    if days_left >= min_days:
        return None
    return (f"🔒 **Certificat HTTPS** de {host} : expire dans {max(days_left, 0):.0f} jour(s). "
            "Le renouvellement automatique (certbot) ne semble pas fonctionner.")


def error_burst_alert(count: int, window_s: float, last_message: str) -> str:
    last = last_message.replace("`", "'")[:200]
    return f"⚠️ **Rafale d'erreurs** : {count} erreurs en {window_s / 60:.0f} min. Dernière : `{last}`"


def reconnect_alert(down_seconds: float) -> str:
    return f"🔌 **Connexion à Discord rétablie** après {down_seconds:.0f} s d'interruption."


def restart_alert(version: str) -> str:
    return ("🔁 **WaseBoard a redémarré** sans s'être arrêté proprement (plantage, coupure de courant ou arrêt brutal). "
            f"Version {version}.")
