# Checklist de bascule finale (prod)

Document vivant : tout ce qui a été volontairement différé pendant le développement du multi-tenant,
pour que le jour J soit « suivre la liste » plutôt que réinventer l'ordre des opérations. Rien ici n'est
fait automatiquement — chaque étape se coche à la main, dans l'ordre, après l'avoir vérifiée.

Portée : la bascule de **cette machine** (prod existante, `waseboard.service`) vers le code de
`feature/multi-tenant`. L'ouverture au public (bot rendu « public » sur le portail Discord, page
d'accueil « Ajouter WaseBoard à mon serveur », annonce) est une étape **suivante**, volontairement hors
de cette liste.

## Avant le jour J

- [ ] **Merger `feature/multi-tenant` dans `master`** (revue finale du diff, ~9 200 lignes) et taguer une
      version. Garder la branche quelques temps après merge, au cas où.
- [ ] **Prévenir les utilisateurs actuels** (message dans le(s) serveur(s) Discord déjà utilisateurs) :
      une coupure de quelques minutes est à prévoir, et tout le monde devra **récupérer un nouveau lien
      de connexion** après coup (le secret partagé change, voir plus bas) — préciser un créneau.
- [ ] **Sauvegarde manuelle immédiate** de `~/waseboard-server/` (en plus du minuteur, voir plus bas) :
      `./deploy/backup.sh -s` (inclut `config.json`/`oauth_sessions.json`, à garder en lieu sûr).
- [ ] Vérifier que `feature/multi-tenant` est toujours vert en CI (`.github/workflows/server-tests.yml`).

## Le jour J, dans l'ordre

1. [ ] **Arrêter le service** : `sudo systemctl stop waseboard`.
2. [ ] **Déployer le nouveau code** (`server.py`, `diagnostics.py`, `wb_config.py`) dans
       `~/waseboard-server/` — mêmes fichiers que ceux déployés et validés sur l'instance de test.
3. [ ] **Nouveau secret partagé**, long et aléatoire (remplace celui de 8 caractères actuel) :
       ```bash
       openssl rand -hex 32   # à coller dans config.json -> "shared_secret"
       ```
       Conséquence immédiate, attendue : tous les clients déjà connectés se déconnectent (401) et
       devront refaire `/configurer-invitation` → nouveau lien. C'est pour ça que ça se prévoit (voir
       « Avant le jour J »), pas une surprise à l'arrivée.
4. [ ] **`public_url` → `https://waseboard.salsi.bid`** dans `config.json` (le site nginx existe déjà,
       voir `/etc/nginx/sites-available/waseboard-salsi-bid` — rien à configurer côté nginx/certbot).
       Garder `waseboard.duckdns.org` actif en parallèle un moment (nginx le sert déjà aussi) : les
       liens déjà distribués pointant vers l'ancien nom continuent de fonctionner le temps que tout le
       monde ait reconfiguré.
5. [ ] **`python3 server.py --check`** : tout au vert avant de relancer le service pour de vrai.
6. [ ] **Démarrer** : `sudo systemctl start waseboard`, puis `curl https://waseboard.salsi.bid/health`.
7. [ ] **`/diagnostic`** dans chaque serveur Discord déjà utilisateur : droits du bot sur chaque salon
       vocal, intent, redirection OAuth2 — tout au vert.
8. [ ] **Fermer l'ancien accès direct** (port 5005, commentaire `# WaseBoard direct (retrocompat, a
       retirer plus tard)` dans `ufw status`) :
       ```bash
       sudo ufw --force delete allow 5005/tcp
       sudo ufw --force delete allow 5005/tcp  # (règle IPv6 séparée)
       ```
       **Avant** de le faire : vérifier qu'aucun client connu n'a encore l'adresse IP brute dans ses
       Paramètres (au lieu du nom de domaine) — sinon le prévenir de corriger avant la fermeture.
9. [ ] **Sauvegardes automatiques en prod**, même principe que sur le test (voir
       `server/tests/README.md`-like : `deploy/waseboard-backup.service`/`.timer`, remplacer
       `VOTRE_USER`/les chemins, `/home/salsi/waseboard-server`, dossier de sortie dédié
       (ex. `~/waseboard-backups`, distinct de `~/waseboard-backups-test`) :
       ```bash
       sudo cp deploy/waseboard-backup.service deploy/waseboard-backup.timer /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now waseboard-backup.timer
       sudo systemctl start waseboard-backup.service   # déclenchement immédiat, pour vérifier tout de suite
       ```
10. [ ] **Republier les liens d'invitation** : `/configurer-invitation` dans chaque salon prévu pour ça,
        et `/panneau` si vous voulez aussi les boutons rapides (voir `docs/FAQ-utilisateurs.md`).
        Prévenir les utilisateurs que le lien a changé (voir étape « Avant le jour J »).

## Après le jour J (dans les heures/jours qui suivent)

- [ ] Surveiller `journalctl -u waseboard -f` un moment après la bascule.
- [ ] Vérifier qu'une sauvegarde automatique prod s'est bien déclenchée le lendemain matin
      (`systemctl list-timers waseboard-backup.timer`, `ls ~/waseboard-backups`).
- [ ] Confirmer avec les utilisateurs que tout fonctionne (nouveaux liens reçus, sons, vocal).
- [ ] Une fois tout le monde migré sur `waseboard.salsi.bid` : envisager de retirer
      `waseboard.duckdns.org` du nginx (pas obligatoire, juste pour alléger).

## Hors de cette liste (étape suivante, plus tard)

Ouverture au public : rendre l'application Discord « publique » sur le portail, page d'accueil
« Ajouter WaseBoard à mon serveur », annonce — décision et calendrier à définir séparément, pas liés à
la bascule technique ci-dessus.
