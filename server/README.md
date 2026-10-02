# Serveur WaseBoard

Héberge le catalogue de sons partagé et le bot Discord qui les joue dans le salon vocal.
Tourne en continu sur une machine Linux accessible en permanence (testé sur Linux Mint/Ubuntu).
Chacun héberge sa propre instance — pas de serveur central fourni avec le projet.

```
[PC utilisateur A]  ──┐
[PC utilisateur B]  ──┼── HTTPS ──►  [Votre serveur]  ──► Discord (voix)
[PC utilisateur C]  ──┘               - catalogue de sons
                                       - bot Discord (multi-serveurs)
```

- Le bot peut être connecté à plusieurs serveurs Discord en même temps.
- Plusieurs sons peuvent jouer en même temps sans s'annuler entre utilisateurs.
- Chaque clic identifie l'utilisateur Discord qui l'a déclenché ; le serveur retrouve seul
  son salon vocal — rien à choisir côté client.

## 1. Créer l'application bot Discord

1. https://discord.com/developers/applications > **New Application**.
2. Onglet **Bot** > **Reset Token** > copiez le token (gardez-le secret).
3. Section **Privileged Gateway Intents**, activez **SERVER MEMBERS INTENT** (obligatoire,
   sans quoi le bot plante au démarrage avec `PrivilegedIntentsRequired`).
4. Désactivez **Public Bot** si vous ne voulez pas que d'autres puissent l'inviter ailleurs.

## 2. Inviter le bot sur votre serveur Discord

1. Onglet **OAuth2 > URL Generator**.
2. Scopes : **bot** + **applications.commands**.
3. Bot Permissions : **Connect** + **Speak**.
4. Ouvrez l'URL générée, choisissez votre serveur, autorisez.

## 3. Installer sur la machine serveur

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv ffmpeg -y

cd ~/waseboard-server   # ou l'emplacement de votre choix
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copiez `config.example.json` vers `config.json` et renseignez :
- `bot_token` : le token de l'étape 1.
- `guild_id` *(recommandé)* : ID de votre serveur Discord (Discord > Paramètres avancés >
  Mode développeur, puis clic droit sur le serveur > Copier l'ID) — active la synchronisation
  instantanée des commandes slash (`/join`, `/leave`...) sur **tous** les serveurs où le bot
  est présent, y compris ceux qu'il rejoindra plus tard ; sans lui, la synchronisation est
  globale et peut mettre jusqu'à 1h à apparaître.
- `shared_secret` : un mot de passe long et aléatoire (32+ caractères) — c'est la seule
  protection de l'API, choisissez-le en conséquence.
- `http_host` : laissez `0.0.0.0`.
- `public_url` : l'adresse par laquelle **les clients WaseBoard** joignent ce serveur (ex:
  `https://waseboard.exemple.com` une fois l'étape 6 faite, ou l'IP locale en test). Sert
  uniquement à construire le lien de connexion cliquable (voir étape 7) — peut rester vide
  en attendant, le reste du serveur fonctionne sans.
- `trash_retention_days` *(optionnel, 30 par défaut)* : nombre de jours pendant lesquels un
  son supprimé reste restaurable (corbeille, voir « Rôles et administration ») avant d'être
  définitivement effacé ; `0` = jamais purgé.

## 4. Activer la connexion Discord (OAuth2)

Nécessaire pour que les utilisateurs se connectent d'un clic dans WaseBoard plutôt que de
coller leur ID Discord à la main.

1. Sur la même application Discord (étape 1), onglet **OAuth2**.
2. Copiez le **Client ID** et le **Client Secret** (bouton "Reset Secret" si besoin) dans
   `config.json` (`oauth2_client_id`/`oauth2_client_secret`).
3. Section **Redirects**, ajoutez exactement (barre oblique finale incluse) :
   `http://127.0.0.1:48899/callback/`
   (le client WaseBoard héberge lui-même un petit serveur local le temps de la connexion —
   rien à exposer publiquement, ça fonctionne même sans nom de domaine).

## 5. Lancer le serveur

```bash
source venv/bin/activate
python3 server.py
```

Pour le garder actif en permanence, utilisez un service systemd :

```ini
# /etc/systemd/system/waseboard.service
[Unit]
Description=Serveur WaseBoard
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/VOTRE_USER/waseboard-server
ExecStart=/home/VOTRE_USER/waseboard-server/venv/bin/python3 server.py
Restart=on-failure
User=VOTRE_USER

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now waseboard
sudo journalctl -u waseboard -f   # suivre les logs
```

## 6. Rendre le serveur accessible en HTTPS

Le `shared_secret` doit passer par une connexion chiffrée. Let's Encrypt exige un nom de
domaine (jamais une IP nue) — [DuckDNS](https://www.duckdns.org/) en fournit un gratuitement.

1. Créez un compte DuckDNS (GitHub/Google) et un sous-domaine (ex: `mon-groupe.duckdns.org`).
   Notez le token affiché.

2. Maintenez l'IP à jour (utile en résidentiel, où elle change parfois) :
   ```bash
   sudo tee /usr/local/bin/duckdns_update.sh > /dev/null <<'EOF'
   #!/bin/bash
   curl -fsS "https://www.duckdns.org/update?domains=VOTRE_SOUS_DOMAINE&token=VOTRE_TOKEN&ip=" \
       -o /var/log/duckdns/duck.log
   EOF
   sudo chmod 700 /usr/local/bin/duckdns_update.sh
   sudo mkdir -p /var/log/duckdns
   ```
   Puis un service + minuteur systemd (`duckdns-update.service` en `Type=oneshot` exécutant ce
   script, `duckdns-update.timer` avec `OnUnitActiveSec=5min`), activés via
   `sudo systemctl enable --now duckdns-update.timer`.

3. nginx en reverse proxy + certificat :
   ```bash
   sudo apt install -y nginx certbot python3-certbot-nginx
   ```
   `/etc/nginx/sites-available/waseboard` :
   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name VOTRE_SOUS_DOMAINE.duckdns.org;

       location / {
           proxy_pass http://127.0.0.1:5005;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           client_max_body_size 64M;   # limite d'upload du serveur
       }
   }
   ```
   ```bash
   sudo ln -s /etc/nginx/sites-available/waseboard /etc/nginx/sites-enabled/waseboard
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d VOTRE_SOUS_DOMAINE.duckdns.org
   ```
   Certbot configure le certificat, la redirection HTTP→HTTPS et son propre renouvellement.

4. Ouvrez le port **443** sur votre box/routeur (TCP entrant), avec une redirection de port
   vers l'IP locale de cette machine si besoin. Le port 5005 n'a pas besoin d'être ouvert :
   nginx en local suffit à faire le lien.

5. **Si nginx sert déjà un autre site** : ajoutez `default_server` au(x) `listen` de ce site
   existant, sinon nginx peut faire atterrir dessus les requêtes à une adresse non reconnue
   (IP nue, sous-domaine inconnu) au lieu de votre site habituel.

6. Chaque client configure `https://VOTRE_SOUS_DOMAINE.duckdns.org` (sans port) comme adresse
   de serveur dans WaseBoard.

## 7. Distribuer le lien de connexion

Une fois `public_url` et `shared_secret` renseignés (étape 3), tapez `/configurer-invitation`
dans un salon Discord — ça poste un bouton persistant (survit aux redémarrages du bot).
Toute personne pouvant voir ce salon peut cliquer dessus pour recevoir, en message visible
d'elle seule, un lien `waseboard://connect?...` qui pré-remplit automatiquement l'adresse et
le jeton d'accès dans WaseBoard (rien à copier-coller). Qui voit ce bouton se règle en
restreignant l'accès au salon via les permissions Discord habituelles — rien à configurer
côté WaseBoard.

## 8. Utilisation

1. Chaque utilisateur installe WaseBoard et clique le lien de connexion reçu via le bouton
   ci-dessus (ou configure manuellement l'adresse/le jeton dans les Paramètres), puis se
   connecte avec Discord lors de l'onboarding.
2. `/join` dans Discord fait rejoindre le bot au salon vocal (ou le bouton "🔊 Rejoindre mon
   vocal" dans WaseBoard). `/leave` pour le déconnecter — il part aussi seul si le salon se vide.
3. Les sons ajoutés/joués depuis n'importe quel client sont partagés entre tous les
   utilisateurs connectés au même serveur.

## Rôles et administration

Les droits sont décidés **par le serveur** (le client ne fait que masquer ce qui est interdit),
à partir de l'identité Discord vérifiée de chaque utilisateur.

**Qui est administrateur d'un serveur Discord ?** Le propriétaire, toute personne ayant la
permission Discord *Administrateur*, et — si un admin le règle dans le panel — les membres
d'un rôle Discord désigné (utile pour déléguer sans donner la permission Administrateur).

| Action | Qui peut |
|---|---|
| Jouer un son, favoris, catégories/raccourcis/volumes personnels | tout membre |
| Ajouter un son | tout membre par défaut ; réglable : réservé aux admins, ou refusé à un membre précis |
| Renommer, changer l'emoji, supprimer un son | son auteur, ou un admin de la guilde où le son a été uploadé |
| Ajouter/retirer un son d'une catégorie partagée | son auteur, ou un admin de cette guilde |

Les sons uploadés avant l'arrivée des rôles n'ont pas d'auteur enregistré : seuls les admins
peuvent les gérer. Un son supprimé va dans une **corbeille** (fichier conservé, partages
mémorisés) : un admin peut le restaurer pendant `trash_retention_days` jours.

**Panel d'administration** (bouton « Administration » dans WaseBoard, visible des seuls admins) :
sons de la guilde (auteur, date, taille, nombre de lectures), corbeille, réglages (rôle admin,
upload réservé aux admins, quotas de nombre/taille/durée, anti-spam à la lecture, membres
bloqués), statistiques d'usage, journal d'actions, lien d'invitation. La limite de durée
nécessite `ffprobe` (fourni avec ffmpeg) ; s'il manque, elle est simplement ignorée.

**Fichiers de données créés à côté de `server.py`** (à inclure dans vos sauvegardes avec
`sounds_data/` et `shared_categories.json`) : `guild_settings.json` (réglages par serveur),
`audit.jsonl` (journal d'actions), `stats.jsonl` (lectures), `sounds_data/trash/` +
`sounds_data/trash.json` (corbeille).

## Sécurité

- Depuis l'ajout de la connexion Discord (OAuth2), l'accès réel au catalogue/aux sons repose
  sur une identité Discord vérifiée + l'appartenance à la bonne guilde — plus seulement sur
  `shared_secret`. Ce dernier protège désormais surtout les quelques routes utilisables avant
  toute connexion (`/status`, `/activity`, `/oauth/client-id`, `/oauth/exchange`), pour éviter
  qu'un inconnu sur internet puisse ne serait-ce que sonder le serveur ou saturer l'échange
  OAuth2 — choisissez-le quand même long et aléatoire, aucune limitation de débit n'existe
  en complément.
- Le lien de connexion (`/configurer-invitation`) contient ce `shared_secret` en clair — qui
  voit le bouton Discord qui le distribue doit donc être contrôlé via les permissions du
  salon où vous le postez, pas seulement en gardant le secret pour vous.

## Dépannage

- **Le bot ne rejoint pas / erreur PyNaCl** : `pip install -r requirements.txt --force-reinstall`.
- **Le son ne se joue pas** : vérifiez `ffmpeg -version` fonctionne sur le serveur.
- **Un client ne peut pas se connecter** : depuis une autre machine,
  `curl https://VOTRE_DOMAINE/status -H "X-WaseBoard-Token: VOTRE_SECRET"`.
- **"Address already in use"** : changez `http_port` dans `config.json`, ou arrêtez l'ancien
  processus (`sudo lsof -i :5005`).
