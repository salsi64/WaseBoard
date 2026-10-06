# Tests du serveur

Vrai code de `server.py` / `diagnostics.py` / `wb_config.py`, avec de fausses interactions Discord (objets
minimalistes qui imitent discord.py) — aucun jeton de bot réel n'est nécessaire. Chaque fichier `test_*.py`
s'exécute indépendamment (`python test_roles.py`) et se termine par un code de sortie 0 (tout est passé) ou
1 (au moins un échec) ; `run_all.sh` les lance tous à la suite et agrège le résultat. Lancés automatiquement
par la CI (`.github/workflows/server-tests.yml`) à chaque push/pull request.

## Lancer les tests

```bash
cd server
pip install -r requirements.txt
sudo apt install ffmpeg libopus0   # sur Debian/Ubuntu ; voir plus bas si absent
./tests/run_all.sh
```

`_env.py` crée, avant l'import de `server.py`, un dossier de données tout neuf et jetable
(`tempfile.mkdtemp()`, via `WASEBOARD_DATA_DIR`) : **rien ne touche jamais une vraie instance déployée**, et
chaque fichier peut être relancé autant de fois que voulu.

`test_diagnostic.py` fait deux vrais appels réseau à `discord.com` (avec un jeton volontairement invalide,
pour vérifier le message d'erreur exact que renvoie Discord) — il faut donc un accès Internet sortant pour
que ces deux vérifications passent ; tout le reste de la suite fonctionne hors ligne.

Sans `ffmpeg`/`libopus0` installés, les vérifications correspondantes de `diagnostics.check_tools()`
(utilisées par `test_diagnostic.py` et `test_capacity.py`) tournent en ❌ au lieu de ✅ — ce n'est pas un
échec de ces fichiers de test, juste un reflet fidèle de l'environnement où ils tournent.

## Contenu

| Fichier | Couvre |
|---|---|
| `test_roles.py` | rôles/permissions, upload/suppression/partage, corbeille, journal, quotas, anti-spam, découpe non destructive |
| `test_replace_file.py` | remplacement du fichier d'un son (`PUT /sounds/<id>/file`) : identité conservée, droits, plafonds, changement d'extension, refus sans effet de bord |
| `test_invite.py` | bouton d'invitation Discord → page `/connect/<code>` → lien `waseboard://` |
| `test_diagnostic.py` | configuration (fichier + variables d'environnement), `/health`, `/diagnostic`, `--check`/`--invite-url` |
| `test_capacity.py` | plafonds de ressources, quotas/plafonds de guilde, limitation de débit par IP, `/instance` |
| `test_panel.py` | panneaux de boutons Discord (`/panneau`, `/configurer-invitation`, `/inviter-bot`, `/bot-setup`) |

Volontairement absent de cette suite : un test de lecture multi-guildes qui lisait le catalogue **réel** de
l'instance de test de l'auteur (`sons_data` d'un déploiement précis, avec ses propres ID de guilde/son) — pas
reproductible ailleurs. Si vous voulez un équivalent général, il reste à écrire avec un catalogue fabriqué
comme le fait déjà `test_roles.py`.

## Écrire un nouveau test

Reprenez le début d'un fichier existant : `import _env` (toujours **avant** `import server as S` — c'est ce
qui fixe le dossier de données isolé), de faux objets Discord minimalistes (`Guild`, `Member`, `Role`...,
voir `test_roles.py`), un helper `check(nom, condition, détail="")` qui accumule dans `results` et imprime
PASS/FAIL, puis `sys.exit(0 if all(results) else 1)` à la fin.
