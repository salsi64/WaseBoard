# Serveur WaseBoard

Héberge le catalogue de sons partagé et le bot Discord qui les joue dans le salon vocal.
Tourne en continu sur une machine Linux accessible en permanence (testé sur Linux Mint/Ubuntu).
Chacun héberge sa propre instance — pas de serveur central fourni avec le projet.

```
[PC utilisateur A]  ──┐
[PC utilisateur B]  ──┼── HTTP (port 5005) ──►  [Votre serveur]  ──► Discord (voix)
[PC utilisateur C]  ──┘                          - catalogue de sons
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
  Mode développeur, puis clic droit sur le serveur > Copier l'ID) — sinon les commandes slash
  mettent jusqu'à 1h à apparaître au lieu d'être instantanées.
- `shared_secret` : un mot de passe long et aléatoire (32+ caractères) — c'est la seule
  protection de l'API, choisissez-le en conséquence.
- `http_host` : laissez `0.0.0.0`.

## 4. Ouvrir le port réseau

Port HTTP (`5005` par défaut), en TCP entrant :

```bash
sudo ufw allow 5005/tcp
```

Si le serveur est derrière une box/routeur, ajoutez aussi une redirection de port (port
forwarding) du port 5005 TCP vers l'IP locale de cette machine.

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

## 6. (Recommandé) Passer en HTTPS avec un nom de domaine gratuit

Sans HTTPS, le `shared_secret` transite en clair sur le réseau. Let's Encrypt exige un nom de
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

4. Ouvrez le port **443** sur votre box/routeur en plus du 5005 (retirable une fois tous les
   clients migrés sur la nouvelle adresse).

5. **Si nginx sert déjà un autre site** : ajoutez `default_server` au(x) `listen` de ce site
   existant, sinon nginx peut faire atterrir dessus les requêtes à une adresse non reconnue
   (IP nue, sous-domaine inconnu) au lieu de votre site habituel.

6. Chaque client configure `https://VOTRE_SOUS_DOMAINE.duckdns.org` (sans port) comme adresse
   de serveur dans WaseBoard.

## 7. Utilisation

1. Chaque utilisateur configure, dans les Paramètres de son WaseBoard : l'adresse du serveur
   (`https://votre-domaine.duckdns.org` si vous avez suivi l'étape 6, sinon
   `http://VOTRE_IP:5005`), le `shared_secret`, et son ID Discord (voir le README principal).
2. `/join` dans Discord fait rejoindre le bot au salon vocal (ou le bouton "🔊 Rejoindre mon
   vocal" dans WaseBoard). `/leave` pour le déconnecter — il part aussi seul si le salon se vide.
3. Les sons ajoutés/joués depuis n'importe quel client sont partagés entre tous les
   utilisateurs connectés au même serveur.

## Sécurité

- Le `shared_secret` est la seule protection de l'API — aucune limitation de débit sur les
  tentatives, choisissez-le long et aléatoire, ne le partagez qu'à des personnes de confiance.
- Sans HTTPS (étape 6), ce secret transite en clair et peut être intercepté.

## Dépannage

- **Le bot ne rejoint pas / erreur PyNaCl** : `pip install -r requirements.txt --force-reinstall`.
- **Le son ne se joue pas** : vérifiez `ffmpeg -version` fonctionne sur le serveur.
- **Un client ne peut pas se connecter** : depuis une autre machine,
  `curl http://VOTRE_IP:5005/status -H "X-WaseBoard-Token: VOTRE_SECRET"`.
- **"Address already in use"** : changez `http_port` dans `config.json`, ou arrêtez l'ancien
  processus (`sudo lsof -i :5005`).
