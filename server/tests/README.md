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
| `test_voice_idle.py` | voyant « en train de parler » du bot : pause du lecteur vocal quand rien ne joue, reprise à l'arrivée d'un son (sans jamais en bloquer un), mixeur lié à la connexion vocale, fonctionnalités annoncées par `/status` |
| `test_pcm_cache.py` | cache des sons décodés en mémoire : plafond/éviction/invalidation, mêmes octets que ffmpeg (avec et sans découpe), démarrage instantané mesuré, volume, mixeur, réglage `pcm_cache_mb` |
| `test_polling_log_filter.py` | journal d'accès : les sondages `/activity` et `/status` réussis ne sont plus journalisés, tout le reste l'est toujours |
| `test_security.py` | jeton partagé comparé à temps constant, expiration des sessions inactives, déconnexion `POST /oauth/logout`, réglage `session_max_idle_days` |
| `test_audio_engine.py` | limiteur doux, boucle sans coupure et plafond de durée, arrêt d'un seul son, fin de plusieurs sons de durées différentes (régression : un son terminé qui n'était pas le dernier restait dans le mixeur) |
| `test_effects.py` | effets (hauteur, vitesse, écho, basses) : validation stricte, chaîne ffmpeg sûre, durée et hauteur mesurées sur un vrai son |
| `test_play_options.py` | `POST /play` avec boucle/effets, `POST /stop` d'un seul son, `POST /play-sequence` (tout ou rien, `after_end`, annulation, anti-spam), droits et réglages |
| `test_membership_cache.py` | cache de résolution des serveurs d'un membre (`find_memberships`) : pas d'appel REST à Discord pour un serveur au cache de membres complet, expiration, isolation entre membres |
| `test_migrate_legacy_catalog.py` | migration ponctuelle du catalogue d'avant l'isolation par serveur (`deploy/migrate_legacy_catalog.py`) sur un jeu de données fabriqué |
| `test_invite.py` | bouton d'invitation Discord → page `/connect/<code>` → lien `waseboard://` |
| `test_diagnostic.py` | configuration (fichier + variables d'environnement), `/health`, `/diagnostic`, `--check`/`--invite-url` |
| `test_capacity.py` | plafonds de ressources, quotas/plafonds de guilde, limitation de débit par IP, `/instance` |
| `test_panel.py` | panneaux de boutons Discord (`/panneau`, `/configurer-invitation`, `/inviter-bot`, `/bot-setup`) |
| `test_slash_commands.py` | commandes `/play` (autocomplétion, droits, salon vocal, anti-spam, plafonds), `/stop`, `/top`, `/bilan` |
| `test_backup.py` | `deploy/backup.sh` : archive des données, archive séparée des secrets (`-c`), droits 600, conservation, refus d'un dossier qui n'est pas WaseBoard |
| `test_install_watchdog.py` | `deploy/install-watchdog.sh` : webhook validé et gardé dans un fichier 600, service/minuteur générés, plusieurs instances, refus des entrées invalides |
| `test_messages.py` | messages bilingues : choix de la langue (`Accept-Language`, langue de Discord), traduction des messages à trous, **couverture** (chaque message d'erreur de `server.py` / `effects.py` et chaque texte envoyé aux membres dans Discord a sa traduction), menu des commandes traduit (limites de Discord), comportement sur HTTP |
| `test_reports.py` | calculs et mise en forme de `/top` et `/bilan` (bilan annuel, rangs, texte sûr) — module `reports.py`, sans Discord |
| `test_alerts.py` | alertes d'exploitation : anti-répétition, rafale d'erreurs, disque, certificat HTTPS, redémarrage inattendu, reconnexion, message privé, configuration |
| `test_web_app.py` | page web `/app` : fichiers servis et en-têtes de sécurité, configuration de connexion, origines autorisées, authentification de l'API par session (sans secret partagé), échange du code Discord depuis la page, déconnexion ; **Activité Discord** : page autorisée dans un cadre (`frame_id`), racine du domaine, origine `discordsays.com`, échange du code sans adresse de retour |
| `test_user_categories.py` | catégories personnelles (copie de celles de l'application Windows) : `GET`/`PUT /me/categories` avec la seule session, isolement entre personnes, remplacement complet, limites et refus, effacement quand il n'y a plus rien, droits du fichier (0600), messages en anglais |
| `test_web_editor.py` | éditeur de sons de la page web : envoi, modification, nouveau fichier, catégories partagées et suppression avec la seule session Discord (vrai serveur HTTP, vrais envois multipart), droits de l'auteur, de l'admin et d'un simple membre |
| `web/*.test.mjs` (Node) | logique pure de la page (couleurs identiques au client Windows, recherche, sections, favoris, OAuth) et de l'éditeur (formulaire, découpe, forme d'onde, doublons, catégories partagées), traductions fr/en, cohérence du paquet (service worker, manifeste, icônes, aucun code en ligne), service worker simulé (cache, hors connexion) — `node --test tests/web/*.test.mjs` |
| `test_watchdog.py` | `deploy/watchdog.sh` : panne confirmée, rappels, retour, webhook en panne, sans webhook (faux `systemctl` et `curl`) |

Volontairement absent de cette suite : un test de lecture multi-guildes qui lisait le catalogue **réel** de
l'instance de test de l'auteur (`sons_data` d'un déploiement précis, avec ses propres ID de guilde/son) — pas
reproductible ailleurs. Si vous voulez un équivalent général, il reste à écrire avec un catalogue fabriqué
comme le fait déjà `test_roles.py`.

## Écrire un nouveau test

Reprenez le début d'un fichier existant : `import _env` (toujours **avant** `import server as S` — c'est ce
qui fixe le dossier de données isolé), de faux objets Discord minimalistes (`Guild`, `Member`, `Role`...,
voir `test_roles.py`), un helper `check(nom, condition, détail="")` qui accumule dans `results` et imprime
PASS/FAIL, puis `sys.exit(0 if all(results) else 1)` à la fin.
