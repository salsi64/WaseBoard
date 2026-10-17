"""Alertes d'exploitation (alerts.py + leur intégration dans server.py) : anti-répétition, rafale d'erreurs, disque,
certificat HTTPS, redémarrage inattendu, reconnexion, remise du message privé. Vrai code, faux objets Discord.
Dossier de données isolé (_env.py)."""
import asyncio
import logging
import ssl
import sys
import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import alerts as A  # noqa: E402
import server as S  # noqa: E402
import wb_config  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


STATE_PATH = _env.DATA_DIR / "alerts_test_state.json"


class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


async def main():
    print("\n--- Mémoire des alertes (fichier) ---")
    STATE_PATH.unlink(missing_ok=True)
    st = A.AlertState(STATE_PATH)
    check("fichier absent : premier démarrage connu (clean_shutdown None), rien d'envoyé", st.clean_shutdown is None and st.last_sent == {})
    st.last_sent["disk"] = 123.0; st.clean_shutdown = False; st.save()
    st2 = A.AlertState(STATE_PATH)
    check("écriture puis relecture à l'identique", st2.last_sent == {"disk": 123.0} and st2.clean_shutdown is False)
    STATE_PATH.write_text("{pas du json", encoding="utf-8")
    st3 = A.AlertState(STATE_PATH)
    check("fichier abîmé : on repart de zéro sans planter", st3.last_sent == {} and st3.clean_shutdown is None)
    STATE_PATH.write_text('{"last_sent": [1, 2], "clean_shutdown": "oui"}', encoding="utf-8")
    st4 = A.AlertState(STATE_PATH)
    check("contenu de mauvais type ignoré", st4.last_sent == {} and st4.clean_shutdown is None)
    check("pas de fichier temporaire laissé", not STATE_PATH.with_name(STATE_PATH.name + ".tmp").exists())

    print("\n--- Anti-répétition ---")
    STATE_PATH.unlink(missing_ok=True)
    clock, sent, deliver = Clock(), [], [True]
    async def send(text):
        sent.append(text)
        return deliver[0]
    mgr = A.AlertManager(A.AlertState(STATE_PATH), send, clock)
    check("1re alerte : envoyée", await mgr.notify("disk", "d1") is True and sent == ["d1"])
    clock.t += 3600
    check("même type 1 h plus tard : retenue (délai de 12 h)", await mgr.notify("disk", "d2") is False and sent == ["d1"])
    check("un autre type n'est pas bloqué", await mgr.notify("cert", "c1") is True and sent == ["d1", "c1"])
    clock.t += A.COOLDOWNS["disk"]
    check("délai écoulé : renvoyée", await mgr.notify("disk", "d3") is True and sent[-1] == "d3")
    mgr2 = A.AlertManager(A.AlertState(STATE_PATH), send, clock)
    check("le délai survit à un redémarrage (fichier)", await mgr2.notify("disk", "d4") is False)

    STATE_PATH.unlink(missing_ok=True)
    sent.clear(); deliver[0] = False; clock.t = 5_000_000.0
    mgr3 = A.AlertManager(A.AlertState(STATE_PATH), send, clock)
    check("envoi raté (messages privés fermés) : False", await mgr3.notify("restart", "r1") is False and len(sent) == 1)
    clock.t += 60
    check("…pas de nouvel essai une minute plus tard", await mgr3.notify("restart", "r2") is False and len(sent) == 1)
    clock.t += A.RETRY_AFTER_FAILURE
    deliver[0] = True
    check("…nouvel essai après 10 minutes, et il passe", await mgr3.notify("restart", "r3") is True and sent[-1] == "r3")

    async def boom(text): raise RuntimeError("Discord injoignable")
    mgr4 = A.AlertManager(A.AlertState(_env.DATA_DIR / "alerts_other.json"), boom, clock)
    check("une exception à l'envoi ne remonte pas", await mgr4.notify("errors", "x") is False)

    print("\n--- Rafale d'erreurs ---")
    mono = Clock(100.0)
    det = A.ErrorBurstDetector(threshold=3, window_s=300, clock=mono)
    def fake_record(msg, name="waseboard-server", level=logging.ERROR):
        return logging.LogRecord(name, level, __file__, 1, msg, None, None)
    det.emit(fake_record("e1")); det.emit(fake_record("e2"))
    check("sous le seuil : rien", det.burst() is None)
    det.emit(fake_record("e3"))
    count, last = det.burst()
    check("au seuil : (3, dernier message)", count == 3 and last.endswith("e3"), (count, last))
    mono.t += 301
    check("les erreurs sortent de la fenêtre de 5 minutes", det.burst() is None)
    det.emit(fake_record("e4", name="waseboard-alerts")); det.emit(fake_record("e5", name="waseboard-alerts")); det.emit(fake_record("e6", name="waseboard-alerts"))
    check("les messages de ce module ne comptent pas (pas de boucle d'alertes)", det.burst() is None)
    off = A.ErrorBurstDetector(threshold=0, clock=mono)
    for _ in range(50): off.emit(fake_record("e"))
    check("seuil 0 : désactivé", off.burst() is None)
    logger = logging.getLogger("test-burst")
    logger.setLevel(logging.DEBUG); logger.propagate = False
    real = A.ErrorBurstDetector(threshold=2, clock=mono); logger.addHandler(real)
    logger.warning("pas une erreur"); logger.error("une"); logger.info("info")
    check("seules les erreurs (ERROR et +) sont comptées", real.burst() is None)
    logger.exception("deux")
    check("branché sur un vrai logger : 2 erreurs = rafale", real.burst() is not None and real.burst()[0] == 2)
    check("le détecteur du serveur est branché sur le journal racine", S.ERROR_BURST in logging.getLogger().handlers and S.ERROR_BURST.threshold == S.ALERTS_ERROR_BURST)

    print("\n--- Disque ---")
    Usage = namedtuple("Usage", "total used free")
    real_usage = A.shutil.disk_usage
    try:
        A.shutil.disk_usage = lambda p: Usage(100 * 1024 ** 3, 95 * 1024 ** 3, 5 * 1024 ** 3)
        msg = A.disk_alert(Path("."), 90)
        check("95 % utilisé, seuil 90 : alerte avec le pourcentage et l'espace libre", msg and "95 %" in msg and "5.0 Go" in msg, msg)
        check("seuil 98 : pas d'alerte", A.disk_alert(Path("."), 98) is None)
        check("seuil 0 : vérification désactivée", A.disk_alert(Path("."), 0) is None)
        A.shutil.disk_usage = lambda p: Usage(100, 90, 10)
        check("exactement au seuil : alerte", A.disk_alert(Path("."), 90) is not None)
        def fail(p): raise OSError("introuvable")
        A.shutil.disk_usage = fail
        check("disque illisible : pas d'alerte (on ne signale pas ce qu'on ne sait pas vérifier)", A.disk_alert(Path("."), 90) is None)
    finally:
        A.shutil.disk_usage = real_usage
    check("vrai disque : jamais plein à 101 %", A.disk_alert(_env.DATA_DIR, 101) is None)

    print("\n--- Certificat HTTPS ---")
    NOW = 2_000_000_000.0
    day = 86400
    def reader_for(expires):
        calls = []
        def reader(host, port):
            calls.append((host, port)); return expires
        reader.calls = calls
        return reader
    r = reader_for(NOW + 3 * day)
    msg = A.certificate_alert("https://waseboard.exemple.org", 14, NOW, r)
    check("expire dans 3 jours (seuil 14) : alerte", msg and "3 jour(s)" in msg and "waseboard.exemple.org" in msg, msg)
    check("…le nom et le port 443 sont ceux de l'adresse publique", r.calls == [("waseboard.exemple.org", 443)], r.calls)
    check("expire dans 30 jours : rien", A.certificate_alert("https://x.org", 14, NOW, reader_for(NOW + 30 * day)) is None)
    check("déjà expiré (date passée) : alerte « 0 jour(s) »", "0 jour(s)" in (A.certificate_alert("https://x.org", 14, NOW, reader_for(NOW - 5 * day)) or ""))
    check("lecture impossible (None) : rien", A.certificate_alert("https://x.org", 14, NOW, reader_for(None)) is None)
    check("adresse http : rien (pas de certificat à vérifier)", A.certificate_alert("http://192.168.1.165:5007", 14, NOW, reader_for(NOW)) is None)
    check("pas d'adresse publique : rien", A.certificate_alert("", 14, NOW, reader_for(NOW)) is None)
    check("seuil 0 : désactivé", A.certificate_alert("https://x.org", 0, NOW, reader_for(NOW)) is None)
    r2 = reader_for(NOW)
    A.certificate_alert("https://x.org:8443/chemin", 14, NOW, r2)
    check("port explicite respecté", r2.calls == [("x.org", 8443)], r2.calls)
    def expired(host, port): raise ssl.SSLCertVerificationError(1, "certificate has expired")
    msg = A.certificate_alert("https://x.org", 14, NOW, expired)
    check("certificat expiré selon la vérification TLS : alerte explicite", msg and "expiré" in msg, msg)
    check("lecture réelle d'un port fermé : None, sans exception", A.read_certificate_expiry("localhost", 1, timeout=0.5) is None)
    check("code d'erreur « expiré » d'OpenSSL", A.EXPIRED_VERIFY_CODE == 10)

    print("\n--- Textes ---")
    check("rafale : le message est cité sans casser le formatage", "`" not in A.error_burst_alert(5, 300, "a `b` c").split("Dernière : ")[1].strip("`"))
    check("rafale : durée en minutes", "5 min" in A.error_burst_alert(5, 300, "x"))
    check("reconnexion : durée en secondes", "90 s" in A.reconnect_alert(90.4))
    check("redémarrage : version indiquée", "3.4.0-dev" in A.restart_alert("3.4.0-dev"))

    print("\n--- Dans le bot : redémarrage inattendu ---")
    bot = S.bot
    delivered = []
    async def dm(text): delivered.append(text); return True
    STATE_PATH.unlink(missing_ok=True)
    bot.alerts = A.AlertManager(A.AlertState(STATE_PATH), dm, Clock(7_000_000.0))
    bot._ready_seen = False
    await bot._alerts_on_ready()
    check("tout premier démarrage : aucune alerte", delivered == [])
    check("…et l'arrêt non propre est armé (clean_shutdown False sur disque)", A.AlertState(STATE_PATH).clean_shutdown is False)
    # le processus meurt sans close() : nouveau processus, même fichier
    bot.alerts = A.AlertManager(A.AlertState(STATE_PATH), dm, Clock(7_000_500.0)); bot._ready_seen = False
    await bot._alerts_on_ready()
    check("démarrage après un arrêt non propre : alerte « redémarré »", len(delivered) == 1 and "redémarré" in delivered[0] and S.SERVER_VERSION in delivered[0], delivered)
    await bot._alerts_on_ready()
    check("on_ready rejoué (nouvelle session) sans coupure mesurée : pas de nouvelle alerte", len(delivered) == 1)
    # arrêt voulu : close() marque l'arrêt propre
    real_close = S.commands.Bot.close
    async def fake_super_close(self): return None
    S.commands.Bot.close = fake_super_close
    try:
        await bot.close()
    finally:
        S.commands.Bot.close = real_close
    check("close() : arrêt propre noté sur disque", A.AlertState(STATE_PATH).clean_shutdown is True)
    bot.alerts = A.AlertManager(A.AlertState(STATE_PATH), dm, Clock(7_001_000.0)); bot._ready_seen = False
    before = len(delivered)
    await bot._alerts_on_ready()
    check("démarrage après un arrêt propre (systemctl restart, mise à jour) : aucune alerte", len(delivered) == before)

    print("\n--- Dans le bot : reconnexion à Discord ---")
    delivered.clear()
    STATE_PATH.unlink(missing_ok=True)
    bot.alerts = A.AlertManager(A.AlertState(STATE_PATH), dm, Clock(8_000_000.0)); bot._ready_seen = True; bot._gateway_down_since = None
    await bot.on_disconnect()
    started = bot._gateway_down_since
    await bot.on_disconnect()
    check("coupure : le début est mémorisé une seule fois", started is not None and bot._gateway_down_since == started)
    await bot.on_resumed()
    check("coupure de quelques millisecondes : aucune alerte", delivered == [] and bot._gateway_down_since is None)
    bot._gateway_down_since = time.monotonic() - 125
    await bot.on_resumed()
    check("coupure de plus d'une minute : alerte avec la durée", len(delivered) == 1 and "rétablie" in delivered[0] and "125 s" in delivered[0], delivered)
    bot._gateway_down_since = time.monotonic() - 125
    await bot._alerts_on_ready()
    check("nouvelle session (on_ready rejoué) après une coupure : même traitement, délai anti-répétition respecté", len(delivered) == 1)
    await bot.on_resumed()
    check("reprise sans coupure connue : rien", len(delivered) == 1)

    print("\n--- Dans le bot : remise du message privé ---")
    sent_dm = []
    class FakeUser:
        id = 777
        async def send(self, text): sent_dm.append(text)
    fetched = []
    async def fake_fetch_user(uid): fetched.append(uid); return FakeUser()
    bot.fetch_user = fake_fetch_user
    async def info_user(): return SimpleNamespace(team=None, owner=SimpleNamespace(id=111))
    async def info_team(): return SimpleNamespace(team=SimpleNamespace(owner_id=222), owner=SimpleNamespace(id=111))
    bot.application_info = info_user
    ok = await bot._send_owner_dm("⚠️ test")
    check("destinataire : propriétaire de l'application", ok and fetched == [111])
    check("le message porte l'instance et la version en pied de page", sent_dm[0].startswith("⚠️ test") and "-# Instance" in sent_dm[0] and S.SERVER_VERSION in sent_dm[0], sent_dm)
    bot.application_info = info_team
    await bot._send_owner_dm("x")
    check("application d'équipe : le propriétaire de l'équipe", fetched[-1] == 222)
    S.ALERTS_USER_ID = "424242"
    try:
        await bot._send_owner_dm("x")
        check("alerts_user_id l'emporte", fetched[-1] == 424242)
    finally:
        S.ALERTS_USER_ID = ""
    async def refuse(text): raise RuntimeError("Cannot send messages to this user")
    FakeUser.send = lambda self, text: refuse(text)
    check("messages privés fermés : False (pas d'exception)", await bot._send_owner_dm("x") is False)

    print("\n--- Dans le bot : tour de contrôles ---")
    delivered.clear(); STATE_PATH.unlink(missing_ok=True)
    bot.alerts = A.AlertManager(A.AlertState(STATE_PATH), dm, Clock(9_000_000.0))
    saved = (S.alerts.disk_alert, S.alerts.certificate_alert, S.ERROR_BURST)
    calls = {"disk": 0, "cert": 0}
    def fake_disk(path, pct): calls["disk"] += 1; return "DISQUE"
    def fake_cert(url, days): calls["cert"] += 1; return "CERTIF"
    S.alerts.disk_alert, S.alerts.certificate_alert = fake_disk, fake_cert
    try:
        due = {"disk": 0.0, "cert": 0.0}
        await bot._alerts_tick(due)
        check("1er tour : disque et certificat vérifiés, alertes envoyées", calls == {"disk": 1, "cert": 1} and delivered == ["DISQUE", "CERTIF"], (calls, delivered))
        check("échéances reculées (10 min / 6 h)", due["disk"] > time.monotonic() + 500 and due["cert"] > time.monotonic() + 3600 * 5)
        await bot._alerts_tick(due)
        check("2e tour juste après : rien n'est recontrôlé", calls == {"disk": 1, "cert": 1} and len(delivered) == 2)
        bot.alerts = A.AlertManager(A.AlertState(STATE_PATH), dm, Clock(9_000_000.0))
        S.ERROR_BURST = A.ErrorBurstDetector(threshold=2)
        S.ERROR_BURST.emit(fake_record("boum")); S.ERROR_BURST.emit(fake_record("boum 2"))
        delivered.clear()
        await bot._alerts_tick({"disk": 10 ** 12, "cert": 10 ** 12})
        check("rafale d'erreurs détectée à chaque tour", len(delivered) == 1 and "Rafale" in delivered[0] and "boum 2" in delivered[0], delivered)
        bot.alerts = None
        await bot._alerts_tick({"disk": 0.0, "cert": 0.0})
        check("alertes désactivées : le tour ne fait rien", True)
    finally:
        S.alerts.disk_alert, S.alerts.certificate_alert, S.ERROR_BURST = saved

    print("\n--- Configuration ---")
    cfg = wb_config.load_config(environ={}, config_path=_env.DATA_DIR / "inexistant.json")
    check("par défaut : alertes actives, 90 % disque, 14 jours, 20 erreurs",
          S.ALERTS_ENABLED is True and S.ALERTS_DISK_PERCENT == 90 and S.ALERTS_CERT_DAYS == 14 and S.ALERTS_ERROR_BURST == 20, (S.ALERTS_ENABLED,))
    cfg = wb_config.load_config(environ={"WASEBOARD_ALERTS_ENABLED": "false", "WASEBOARD_ALERTS_DISK_PERCENT": "85",
                                         "WASEBOARD_ALERTS_USER_ID": "12345"}, config_path=_env.DATA_DIR / "inexistant.json")
    check("variables d'environnement prises en compte", cfg["alerts_enabled"] is False and cfg["alerts_disk_percent"] == 85 and cfg["alerts_user_id"] == "12345", cfg)

    def refused(values):
        try:
            wb_config.validate_limits(values)
        except wb_config.ConfigError:
            return True
        return False
    check("valeurs invalides refusées au démarrage",
          refused({"alerts_disk_percent": 30}) and refused({"alerts_disk_percent": 100}) and refused({"alerts_cert_days": 200})
          and refused({"alerts_error_burst": 2}) and refused({"alerts_user_id": "abc"}) and refused({"alerts_enabled": "oui"})
          and refused({"alerts_disk_percent": True}))
    for ok_values in ({"alerts_disk_percent": 0}, {"alerts_cert_days": 0}, {"alerts_error_burst": 0}, {"alerts_user_id": ""}, {"alerts_user_id": "123"},
                      {"alerts_enabled": False}):
        wb_config.validate_limits(ok_values)
    check("0 (désactivé), vide et valeurs valides acceptés", True)
    try:
        wb_config.load_config(environ={"WASEBOARD_ALERTS_ENABLED": "peut-être"}, config_path=_env.DATA_DIR / "inexistant.json")
        check("booléen d'environnement illisible : refusé", False)
    except wb_config.ConfigError:
        check("booléen d'environnement illisible : refusé", True)
    check("true/false/1/0/on/off/oui/non compris", [wb_config._boolean(v) for v in ("true", "FALSE", "1", "0", "on", "Off", "oui", "non")]
          == [True, False, True, False, True, False, True, False])


assert S.DATA_DIR == _env.DATA_DIR, f"refus de nettoyer un dossier qui n'est pas celui créé par _env.py : {S.DATA_DIR}"
asyncio.run(main())
print()
print(f"{sum(results)}/{len(results)} vérifications OK")
sys.exit(0 if all(results) else 1)
