# Checklist de bascule finale (prod)

Document vivant : tout ce qui a été volontairement différé pendant le développement du multi-tenant,
pour que le jour J soit « suivre la liste » plutôt que réinventer l'ordre des opérations. Rien ici n'est
fait automatiquement — chaque étape se coche à la main, dans l'ordre, après l'avoir vérifiée.

Portée : la bascule de **cette machine** (prod existante, `waseboard.service`) vers le code de
`feature/multi-tenant`, **client compris** (voir pourquoi juste en dessous). L'ouverture au public (bot
rendu « public » sur le portail Discord, page d'accueil « Ajouter WaseBoard à mon serveur », annonce) est
une étape **suivante**, volontairement hors de cette liste.

## Pourquoi client et serveur basculent ensemble (pas l'un avant l'autre)

Un simple `git merge` de `feature/multi-tenant` dans `master` ne change **rien** pour les utilisateurs :
`UpdateCheckService` (toast « nouvelle version ») compare la version installée à la dernière **release
GitHub** (tag `vX.Y.Z`), jamais au contenu de `master` — merger ne crée aucune release.

La vraie contrainte est ailleurs : côté serveur, presque toutes les routes utiles (`/sounds`, `/play`,
upload, admin…) exigent maintenant une **session OAuth2 Discord** (`_require_session`). Un **ancien**
client n'a aucun moyen d'obtenir cette session (pas d'écran de connexion Discord) : une fois le serveur
basculé, il ne reçoit pas qu'une absence de notification, il **cesse de fonctionner** (liste des sons,
lecture, upload : 401 partout, message d'erreur générique). Symétriquement, un **nouveau** client publié
*avant* la bascule du serveur serait tout aussi cassé : son écran de connexion appelle `/oauth/client-id`
et `/oauth/exchange`, qui n'existent pas sur l'ancien serveur. Conclusion : le nouveau client doit être
**prêt à publier avant de toucher au serveur**, et sa release (étape 6 bis) **immédiatement après** avoir
confirmé que le serveur tourne — pas avant, pas des heures après.

## Pourquoi le catalogue existant a besoin d'une migration (étape 2 bis)

Les 107 sons de prod ont été uploadés avant l'isolation par guilde : aucun n'a de champ `guild_id`. Le
nouveau serveur filtre le catalogue par appartenance à une guilde Discord (`sound_visible_to`) — sans
intervention, ces 107 sons restent sur le disque, intacts, mais **deviennent invisibles et injouables
pour tout le monde** dès le premier démarrage du nouveau code. Vos favoris/catégories/raccourcis locaux
pointant vers ces sons ne sont pas perdus non plus, mais s'affichent dans le vide puisque le son
correspondant n'apparaît plus dans ce que le serveur renvoie.

Complication découverte en vérifiant (lecture seule, API Discord) : le bot de prod est réellement présent
sur **5 serveurs Discord** (Ballistic Potatoes le vrai officiel vevo, Dadiboule, KICOU GAMING, CM_RL,
WaseBoard), qui partagent aujourd'hui un seul et même catalogue — l'ancien code n'a jamais eu de notion
de guilde « propriétaire ». Rattacher les 107 sons à une seule guilde (même en configurant `guild_id`
dans `config.json`, le mécanisme de rétrocompatibilité prévu à l'origine) les aurait rendus invisibles
pour les quatre autres. Décision prise avec l'utilisateur après avoir vérifié le recoupement réel des
membres (certains étaient supposés être aussi sur Ballistic Potatoes ; en pratique seulement 18 % de
KICOU GAMING et CM_RL le sont vraiment) : propriétaire natif = Ballistic Potatoes, **partagé explicitement
vers les 4 autres** (`shared_categories.json`), pour que rien ne change pour personne. Détail et commande
exacte : étape 2 bis ci-dessous. Script : `deploy/migrate_legacy_catalog.py`, testé sur une copie des
vraies données de prod avant d'écrire cette étape (jamais exécuté sur la prod elle-même).

## Avant le jour J

- [ ] **Merger `feature/multi-tenant` dans `master`** (revue finale du diff, ~9 200 lignes). Garder la
      branche quelques temps après merge, au cas où.
- [ ] **Préparer la release du client** (sans la publier) : incrémenter `MyAppVersion` dans
      `installer/setup.iss` **et** `<Version>` dans `src/WaseBoard/WaseBoard.csproj` (doivent rester
      synchronisés, voir le commentaire dans le `.csproj`), `dotnet publish src/WaseBoard/WaseBoard.csproj
      -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true`, compiler l'installeur avec
      Inno Setup. Config `Release` (jamais `Dev`) : titre, nom de l'exe et dossier AppData redeviennent
      automatiquement « WaseBoard » sans rien à changer à la main (`AppIdentity.cs`).
- [ ] **Prévenir les utilisateurs actuels** (message dans le(s) serveur(s) Discord déjà utilisateurs) :
      une coupure de quelques minutes est à prévoir, **l'ancien client cessera de fonctionner** (pas
      juste « pas de notification ») tant qu'ils n'auront pas installé la nouvelle version et récupéré un
      nouveau lien de connexion (le secret partagé change, voir plus bas) — préciser un créneau et donner
      le lien de téléchargement direct à l'avance.
- [ ] **Sauvegarde manuelle immédiate** de `~/waseboard-server/` (en plus du minuteur, voir plus bas) :
      `./deploy/backup.sh -s` (inclut `config.json`/`oauth_sessions.json`, à garder en lieu sûr).
- [ ] Vérifier que `feature/multi-tenant` est toujours vert en CI (`.github/workflows/server-tests.yml`).

## Le jour J, dans l'ordre

1. [ ] **Arrêter le service** : `sudo systemctl stop waseboard`.
2. [ ] **Déployer le nouveau code** (`server.py`, `diagnostics.py`, `wb_config.py`) dans
       `~/waseboard-server/` — mêmes fichiers que ceux déployés et validés sur l'instance de test.
2bis. [ ] **Migrer le catalogue existant** (107 sons, aucun n'a de `guild_id` : sans cette étape, ils
       deviennent invisibles pour tout le monde — voir l'explication complète plus bas). Le bot de prod
       sert réellement **5 serveurs Discord** avec un seul catalogue partagé aujourd'hui ; décision prise
       avec l'utilisateur : propriétaire natif **Ballistic Potatoes le vrai officiel vevo**, partagé
       explicitement vers les 4 autres (continuité totale, personne ne perd rien). Testé sur une copie des
       vraies données de prod avant d'écrire cette étape (337+1 vérifications, voir le détail dans l'historique
       Git) — toujours relancer d'abord **sans** `--apply` pour relire l'aperçu :
       ```bash
       cd ~/waseboard-server
       python3 deploy/migrate_legacy_catalog.py --data-dir . \
         --native-guild-id 329938187025776642 \
         --share-to 326491598869495809,690909467436384308,1344729394249338985,1554898433649807441
       # Relire l'aperçu (107 migrés, 4×107 partagés attendus), PUIS seulement :
       python3 deploy/migrate_legacy_catalog.py --data-dir . \
         --native-guild-id 329938187025776642 \
         --share-to 326491598869495809,690909467436384308,1344729394249338985,1554898433649807441 \
         --apply
       ```
       Guildes (pour mémoire, mêmes IDs que ci-dessus) : Ballistic Potatoes `329938187025776642` (natif) ·
       Dadiboule `326491598869495809` · KICOU GAMING `690909467436384308` · CM_RL `1344729394249338985` ·
       WaseBoard `1554898433649807441`. Sans risque à rejouer : un son déjà migré n'est jamais retouché, le
       partage est une union (rien n'est retiré de ce qui existe déjà dans `shared_categories.json`).
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
6bis. [ ] **Publier la release du client** préparée plus haut : `gh release create vX.Y.Z
       installer/Output/WaseBoard-Setup-X.Y.Z.exe --title "WaseBoard X.Y.Z" --notes "..."` — tout de suite
       après l'étape 6, pas avant (voir « Pourquoi client et serveur basculent ensemble »). C'est cette
       release, pas le merge, qui déclenche le toast « nouvelle version » chez les utilisateurs déjà
       installés (`UpdateCheckService`, comparaison au tag `vX.Y.Z`).
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
- [ ] Confirmer avec les utilisateurs que tout fonctionne (nouveau client installé, nouveaux liens reçus,
      sons, vocal).
- [ ] Une fois tout le monde migré sur `waseboard.salsi.bid` : envisager de retirer
      `waseboard.duckdns.org` du nginx (pas obligatoire, juste pour alléger).

## Hors de cette liste (étape suivante, plus tard)

Ouverture au public : rendre l'application Discord « publique » sur le portail, page d'accueil
« Ajouter WaseBoard à mon serveur », annonce — décision et calendrier à définir séparément, pas liés à
la bascule technique ci-dessus.
