"""Bouton d'invitation Discord -> bouton-lien https -> page /connect/<code> -> lien waseboard://.
Vrai code de server.py (InviteView, _handle_connect_page), fausses interactions Discord. Lancé via tests/run_all.sh (dossier de données isolé et jetable, voir _env.py)."""
import asyncio
import re
import sys
import time

import _env  # noqa: E402  (doit s'exécuter AVANT import server : fixe WASEBOARD_DATA_DIR)
import server as S  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


class FakeResponse:
    def __init__(self): self.sent = []
    async def send_message(self, content=None, **kw): self.sent.append((content, kw))

class FakeInteraction:
    def __init__(self):
        self.response = FakeResponse()

class FakeReq:
    def __init__(self, code): self.match_info = {"code": code}


SECRET = "s3cr/et+&=é"
S.PUBLIC_URL = "https://exemple.test"
S.SHARED_SECRET = SECRET


async def click():
    view = S.InviteView()
    inter = FakeInteraction()
    await view.send_link.callback(inter)
    (content, kw), = inter.response.sent
    return content, kw


async def main():
    print("--- Panneau InviteView : bouton de téléchargement statique ---")
    panel = S.InviteView()
    panel_children = list(panel.children)
    check("2 éléments : téléchargement (lien statique) + connexion directe (callback)", len(panel_children) == 2, panel_children)
    download_btn = next((b for b in panel_children if b.style.name == "link"), None)
    check("bouton de téléchargement : style lien, pointe vers DOWNLOAD_URL, pas de custom_id",
          download_btn is not None and download_btn.url == S.DOWNLOAD_URL and download_btn.custom_id is None, download_btn)
    connect_btn = next((b for b in panel_children if b.style.name != "link"), None)
    check("bouton de connexion : « Connexion directe à ce serveur », custom_id fixe",
          connect_btn is not None and connect_btn.label == "🚀 Connexion directe à ce serveur"
          and connect_btn.custom_id == "waseboard_invite_button", connect_btn)

    print("\n--- Clic sur le bouton de connexion directe ---")
    content, kw = await click()
    link_view = kw["view"]
    buttons = list(link_view.children)
    check("réponse éphémère", kw.get("ephemeral") is True)
    check("un seul bouton, de style lien", len(buttons) == 1 and buttons[0].style.name == "link", [b.style for b in buttons])
    url = buttons[0].url
    m = re.fullmatch(r"https://exemple\.test/connect/([A-Za-z0-9_-]{20,})", url)
    check("le bouton pointe vers PUBLIC_URL/connect/<code> (vrai https cliquable)", m is not None, url)
    code = m.group(1)
    check("le code est valide", S.invite_code_is_valid(code))
    check("le message ne contient NI le secret NI un lien waseboard:// (rien de fuitable en texte)",
          SECRET not in content and "waseboard://" not in content and "s3cr" not in content, content)
    check("mentionne la durée (pas de téléchargement en texte : c'est un bouton séparé du panneau)",
          "15 minutes" in content and "<https://" not in content, content)

    print("\n--- Page /connect/<code> ---")
    bot = S.bot
    resp = await bot._handle_connect_page(FakeReq(code))
    body = resp.text
    expected_link = "waseboard://connect?url=https%3A%2F%2Fexemple.test&token=" + S.quote(SECRET, safe="")
    check("code valide : 200 + HTML", resp.status == 200 and resp.content_type == "text/html", (resp.status, resp.content_type))
    check("la page contient le lien de connexion (attribut HTML échappé)", expected_link.replace("&", "&amp;") in body, body[:300])
    check("redirection automatique par script avec le même lien", f'location.href={S.json.dumps(expected_link)}' in body)
    check("en-têtes : pas de cache, pas de referrer",
          resp.headers["Cache-Control"] == "no-store" and resp.headers["Referrer-Policy"] == "no-referrer" and "noindex" in resp.headers["X-Robots-Tag"])
    nonce = re.search(r'<script nonce="([^"]+)"', body).group(1)
    csp = resp.headers["Content-Security-Policy"]
    check("CSP : script limité au nonce de la page, rien d'autre", f"script-src 'nonce-{nonce}'" in csp and "default-src 'none'" in csp, csp)
    check("le code reste utilisable pendant sa durée (rechargement, permission du navigateur)", (await bot._handle_connect_page(FakeReq(code))).status == 200)

    print("\n--- Codes inconnus, expirés, config absente ---")
    r = await bot._handle_connect_page(FakeReq("n-importe-quoi"))
    check("code inconnu : 404 sans rien révéler", r.status == 404 and "waseboard://" not in r.text and SECRET not in r.text and "s3cr" not in r.text, r.status)
    S.INVITE_CODES[code] = time.time() - 1
    r = await bot._handle_connect_page(FakeReq(code))
    check("code expiré : 404", r.status == 404 and "n'est plus valide" in r.text)
    c2 = S.create_invite_code()
    check("un code expiré est purgé à la création suivante", code not in S.INVITE_CODES and c2 in S.INVITE_CODES)
    S.PUBLIC_URL = ""
    r = await bot._handle_connect_page(FakeReq(c2))
    check("sans public_url : 404 même avec un code valide", r.status == 404)
    content, kw = await click()
    check("sans public_url : le bouton le dit au lieu d'émettre un lien", "non configuré" in content and "view" not in kw, content)
    S.PUBLIC_URL = "https://exemple.test"

    print("\n--- Robustesse ---")
    S.DOWNLOAD_URL = 'https://x.test/"><script>alert(1)</script>'
    page = S.render_connect_page(expected_link, "n0nce")
    check("l'URL de téléchargement est échappée (pas d'injection HTML)", "<script>alert(1)" not in page and "&lt;script&gt;" in page)
    S.INVITE_CODES.clear()
    for _ in range(1100): S.create_invite_code()
    check("garde-fou mémoire : jamais plus de 1000 codes", len(S.INVITE_CODES) <= 1000, len(S.INVITE_CODES))
    codes = {S.create_invite_code() for _ in range(50)}
    check("codes tous différents", len(codes) == 50)


asyncio.run(main())
print("\nRÉSULTAT :", "TOUT PASSE" if all(results) else f"{results.count(False)} échec(s) sur {len(results)}", f"({len(results)} vérifications)")
