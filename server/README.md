# Serveur WaseBoard

Serveur central : héberge le catalogue de sons partagé (upload/liste/suppression) et le bot
Discord qui les joue dans le salon vocal. Prévu pour tourner en continu sur une machine
dédiée (ici : `VOTRE_IP`, Linux Mint), accessible par des clients WaseBoard qui ne sont
**pas** sur le même réseau local.

## Architecture en un coup d'œil

```
[PC utilisateur A]  ──┐
[PC utilisateur B]  ──┼── HTTP (port 5005) ──►  [Serveur VOTRE_IP]  ──► Discord (voix)
[PC utilisateur C]  ──┘                          - catalogue de sons
                                                  - fichiers audio
                                                  - bot Discord (multi-serveurs)
```

Chaque client WaseBoard récupère la liste des sons, en ajoute de nouveaux, et déclenche leur
lecture — tout passe par ce serveur. Le bot Discord, lui, se contente de jouer les sons dans
le(s) salon(s) vocal(aux) sur commande.

**Multi-Discord** : le bot peut être connecté à plusieurs serveurs Discord différents en même
temps (`/join` fonctionne indépendamment sur chacun). Chaque client WaseBoard choisit, dans
ses Paramètres, quel serveur Discord ses clics doivent cibler.

**Plusieurs sons en même temps** : un mixeur audio persistant tourne en continu sur chaque
salon vocal connecté. Jouer un son l'ajoute au mixage au lieu de remplacer ce qui est en
cours — donc deux utilisateurs (ou le même utilisateur, plusieurs fois) peuvent déclencher
des sons simultanément sans s'annuler mutuellement.

**Plus de sélection manuelle du serveur Discord** : chaque clic identifie l'utilisateur
Discord qui l'a déclenché, et le serveur retrouve automatiquement le bon salon vocal en
cherchant où cette personne est actuellement connectée. Rien à configurer côté client.

**Activité partagée en temps réel** : le serveur retient qui a joué quoi dans les dernières
secondes (`GET /activity`), pour que tous les clients WaseBoard affichent le même highlight
et les mêmes avatars au même moment.

**Arrêt global** (`POST /stop`) : coupe immédiatement tous les sons en cours sur le salon
vocal ciblé, sans déconnecter le bot.

## ⚠️ Nouvelle étape obligatoire : activer l'intent "Server Members"

Cette mise à jour ajoute la liste des membres Discord (pour que chaque utilisateur WaseBoard
indique qui il est). Ça nécessite un **intent privilégié**, à activer manuellement :

1. https://discord.com/developers/applications > votre application > onglet **Bot**.
2. Section **Privileged Gateway Intents**, activez **SERVER MEMBERS INTENT**.
3. Sauvegardez.

Sans cette étape, le bot plantera au démarrage avec une erreur `PrivilegedIntentsRequired`.

## 1. Créer l'application bot Discord

1. https://discord.com/developers/applications > **New Application**.
2. Onglet **Bot** > **Reset Token** > copiez le token (gardez-le secret).
3. Désactivez **Public Bot** si vous ne voulez pas que d'autres puissent l'inviter ailleurs.

## 2. Inviter le bot sur votre serveur Discord

1. Onglet **OAuth2 > URL Generator**.
2. Scopes : **bot** + **applications.commands**.
3. Bot Permissions : **Connect** + **Speak**.
4. Ouvrez l'URL générée, choisissez votre serveur, autorisez.

## 3. Installer sur la machine serveur (Linux Mint)

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
  Mode développeur, puis clic droit sur le serveur > Copier l'ID) — sans ça, les commandes
  slash mettent jusqu'à 1h à apparaître au lieu d'être instantanées.
- `shared_secret` : un mot de passe de votre choix. **Chaque utilisateur WaseBoard devra le
  connaître** pour se connecter au serveur (c'est la seule protection contre un accès non
  autorisé, puisque le serveur est exposé sur internet — gardez-le secret, changez-le si
  besoin).
- `http_host` : laissez `0.0.0.0` (écoute sur toutes les interfaces réseau, nécessaire pour
  que des utilisateurs distants puissent se connecter).

## 4. Ouvrir le port réseau

Un seul port à ouvrir : le port HTTP (`5005` par défaut), en **TCP entrant**, vers cette
machine. Deux niveaux à vérifier :

1. **Pare-feu de la machine** (si `ufw` est actif) :
   ```bash
   sudo ufw allow 5005/tcp
   ```
2. **Box/routeur internet** (si le serveur est derrière une box) : redirection de port
   (port forwarding) du port 5005 TCP vers l'IP locale de cette machine sur votre réseau.
   Si `VOTRE_IP` est déjà l'IP publique directe du serveur (hébergement dédié/VPS), cette
   étape ne s'applique pas.

Le trafic vocal Discord lui-même (UDP) ne nécessite **aucune ouverture de port entrant** :
c'est le bot qui se connecte *vers* Discord, jamais l'inverse.

## 5. Lancer le serveur

```bash
source venv/bin/activate
python3 server.py
```

Vous devriez voir :
```
Commandes slash synchronisées sur le serveur ... (instantané).
Serveur HTTP prêt sur 0.0.0.0:5005 (catalogue + ordres de lecture WaseBoard).
```

Pour le garder actif en permanence (recommandé), utilisez un service systemd :

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
sudo journalctl -u waseboard -f   # pour suivre les logs
```

## 6. Utilisation

1. Chaque utilisateur configure l'adresse (`http://VOTRE_IP:5005`), le `shared_secret` et
   son ID Discord dans les Paramètres de son WaseBoard (voir "Identité Discord" dans le README
   principal). Rien d'autre à choisir : le serveur cible (guild) est déduit automatiquement à
   partir du salon vocal où cet utilisateur se trouve — aucun ID de serveur à renseigner
   manuellement, même si le bot est présent sur plusieurs Discords en même temps.
2. Dans chaque Discord concerné, `/join` fait rejoindre le bot au salon vocal où vous êtes
   (ou utilisez le bouton "🔊 Rejoindre mon vocal" directement depuis WaseBoard).
3. Les sons ajoutés/joués depuis n'importe quel client WaseBoard sont partagés avec tous les
   autres utilisateurs connectés au même serveur.
4. `/leave` pour déconnecter le bot d'un salon donné. Il se déconnecte aussi
   **automatiquement** dès qu'il ne reste plus que des bots (ou personne) dans le salon.

## Sécurité — points à garder en tête

- Le serveur est exposé sur internet avec pour seule protection le `shared_secret` en en-tête
  HTTP, sur une connexion **non chiffrée** (HTTP, pas HTTPS). Convenable pour un usage entre
  amis/petite communauté, mais quelqu'un interceptant le trafic réseau pourrait lire ce jeton.
  Pour aller plus loin : placez un reverse proxy (nginx + Let's Encrypt/certbot) devant ce
  serveur pour passer en HTTPS.
- N'importe qui connaissant le `shared_secret` peut uploader, supprimer et faire jouer des
  sons. Ne le partagez qu'avec les personnes de confiance.

## Dépannage

- **Le bot ne rejoint pas / erreur PyNaCl** : `pip install -r requirements.txt --force-reinstall`.
- **Le son ne se joue pas** : vérifiez `ffmpeg -version` fonctionne sur le serveur.
- **Un client WaseBoard ne peut pas se connecter** : vérifiez le port ouvert avec, depuis une
  autre machine, `curl http://VOTRE_IP:5005/status -H "X-WaseBoard-Token: VOTRE_SECRET"`.
- **"Address already in use"** : un autre processus utilise déjà le port 5005 — changez
  `http_port` dans `config.json`, ou arrêtez l'ancien processus (`sudo lsof -i :5005`).
