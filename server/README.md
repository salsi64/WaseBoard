# Serveur WaseBoard

Héberge le catalogue de sons partagé et le bot Discord qui les joue dans le salon vocal.
Tourne en continu sur une machine accessible en permanence (Linux recommandé ; Windows et macOS
via Docker Desktop). Chacun héberge sa propre instance — pas de serveur central fourni avec le projet.

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

**Deux façons d'installer** : l'[installation rapide avec Docker](#installation-rapide-docker)
(recommandée : HTTPS automatique, un assistant qui vérifie tout) ou l'[installation manuelle](#installation-manuelle-avancé)
(Python + systemd + nginx, sans Docker).

## Installation rapide (Docker)

Il vous faut : une machine allumée en permanence avec [Docker](https://docs.docker.com/engine/install/)
(Docker Desktop sous Windows/macOS), et pour un accès depuis Internet un nom de domaine : soit un sous-domaine gratuit
[DuckDNS](https://www.duckdns.org/) dont les ports **80 et 443** sont redirigés vers cette machine, soit un domaine géré par
**Cloudflare** (alors seul le port 443 est nécessaire, voir « Domaine chez Cloudflare » plus bas).

1. **Créez l'application Discord** — [étape 1](#1-créer-lapplication-bot-discord) ci-dessous : jeton du bot,
   « Server Members Intent » activé, puis (étape 4) secret OAuth2 et redirection
   `http://127.0.0.1:48899/callback/`. Vous n'avez **pas** à fabriquer l'URL d'invitation ni à copier le Client ID :
   l'assistant s'en charge.
2. **Récupérez le serveur** : `git clone https://github.com/salsi64/WaseBoard.git` puis `cd WaseBoard/server`
   (ou téléchargez le dossier `server`).
3. **Lancez l'assistant** :
   - Linux / macOS : `./setup.sh`
   - Windows (PowerShell) : `.\setup.ps1` (si l'exécution est bloquée : `powershell -ExecutionPolicy Bypass -File .\setup.ps1`)

   Il demande le jeton du bot, le secret OAuth2 et le nom de domaine, génère le secret partagé, écrit `.env`, puis
   **vérifie la configuration** (jeton, intent, redirection OAuth2, ffmpeg...) et affiche l'**URL pour inviter le bot**
   sur votre serveur Discord. Une fois tout au vert il démarre le serveur et attend qu'il soit prêt.
4. **Dans Discord** : `/diagnostic` (contrôle des droits du bot sur vos salons vocaux), puis `/configurer-invitation`
   dans le salon où vos membres récupèrent leur lien (voir [étape 7](#7-distribuer-le-lien-de-connexion)).

Ce que l'assistant met en place : le serveur (image construite depuis ce dossier, utilisateur sans privilèges,
redémarrage automatique), **Caddy** pour le HTTPS (certificat Let's Encrypt obtenu et renouvelé tout seul) et,
si vous le souhaitez, un conteneur **DuckDNS** (ou **Cloudflare DDNS**) qui garde votre nom de domaine à jour.
Quatre modes : Internet avec HTTPS (défaut), Internet avec HTTPS via un domaine Cloudflare, réseau local sans HTTPS
(test), ou votre propre reverse proxy (le serveur écoute alors sur `127.0.0.1:5005`). Tout tient dans `.env` (modèle
commenté : `.env.example`) — vous pouvez aussi le remplir à la main et lancer `docker compose up -d`.

### Domaine chez Cloudflare (sans port 80)

Si votre domaine est géré par Cloudflare, choisissez le mode 2 de l'assistant. Le certificat Let's Encrypt est alors
prouvé par un enregistrement DNS créé via l'API Cloudflare : **aucun port 80 à ouvrir**, et la machine n'a même pas besoin
d'être joignable au moment de l'émission. Il faut :

1. **Un jeton API limité à votre domaine** : Cloudflare > Mon profil > Jetons API > Créer un jeton > modèle « Modifier le
   DNS de la zone » ; ajoutez la permission *Zone > Zone > Lire* ; « Ressources de la zone » : *Inclure > Zone
   spécifique* > votre domaine. Copiez le jeton (il n'est affiché qu'une fois) : l'assistant vous le demande, il est
   stocké dans `.env` et n'est utilisé que pour créer les enregistrements DNS temporaires de la validation.
2. **Un enregistrement DNS** pour le nom choisi (ex : `waseboard`) : type A vers l'adresse de la machine, « DNS uniquement »
   (nuage gris). Ou laissez l'assistant activer **Cloudflare DDNS**, qui le crée et le met à jour avec votre IP publique.
3. **Le port 443** (TCP) redirigé vers la machine. Pour cohabiter avec un serveur web qui occupe déjà le 443, choisissez
   un autre port (`WASEBOARD_HTTPS_PORT`) : l'adresse donnée aux clients est alors `https://votre-domaine:NUMERO`.

L'image Caddy de ce mode est construite sur place (compilation du module Cloudflare, quelques minutes la première fois).

**Au quotidien**

| Besoin | Commande (dans `server/`) |
|---|---|
| Journal en direct | `docker compose logs -f waseboard` |
| Arrêter / démarrer | `docker compose stop` / `docker compose up -d` |
| Mettre à jour | `git pull` puis `docker compose up -d --build` |
| Vérifier la configuration | `docker compose exec waseboard python server.py --check` (ou `/diagnostic` dans Discord) |
| Modifier la configuration | éditer `.env` puis `docker compose up -d` (ou relancer l'assistant) |

**Données et sauvegarde.** Tout ce qui est précieux (catalogue, sons, sessions, réglages, statistiques, corbeille)
est dans le volume Docker `waseboard_waseboard-data` — il survit aux mises à jour et à `docker compose down`
(**jamais** `docker compose down -v`, qui l'efface). Sauvegarde d'une archive :

```bash
docker run --rm -v waseboard_waseboard-data:/data -v "$PWD":/backup alpine \
    tar czf /backup/waseboard-data-$(date +%F).tgz -C /data .
```

Pensez aussi à garder `.env` (jeton, secrets) en lieu sûr. Les certificats HTTPS sont dans le volume `caddy-data`
(refaits automatiquement s'ils sont perdus).

## Installation manuelle (avancé)

Les étapes 1 à 8 ci-dessous décrivent une installation sans Docker (les étapes 1, 2, 4 et 7 valent aussi pour Docker).

## 1. Créer l'application bot Discord

1. https://discord.com/developers/applications > **New Application**.
2. Onglet **Bot** > **Reset Token** > copiez le token (gardez-le secret).
3. Section **Privileged Gateway Intents**, activez **SERVER MEMBERS INTENT** (obligatoire,
   sans quoi le bot plante au démarrage avec `PrivilegedIntentsRequired`).
4. Désactivez **Public Bot** si vous ne voulez pas que d'autres puissent l'inviter ailleurs.

## 2. Inviter le bot sur votre serveur Discord

Le plus simple : `python3 server.py --invite-url` (ou, avec Docker, l'URL affichée par l'assistant) donne l'URL
d'invitation toute prête, avec les bons scopes et droits. À la main :

1. Onglet **OAuth2 > URL Generator**.
2. Scopes : **bot** + **applications.commands**.
3. Bot Permissions : **View Channels** + **Connect** + **Speak**.
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
- `download_url` *(optionnel)* : où télécharger WaseBoard, proposé sur la page d'invitation (étape 7) ;
  par défaut la dernière version publiée sur GitHub.
- `trash_retention_days` *(optionnel, 30 par défaut)* : nombre de jours pendant lesquels un
  son supprimé reste restaurable (corbeille, voir « Rôles et administration ») avant d'être
  définitivement effacé ; `0` = jamais purgé.

## 4. Activer la connexion Discord (OAuth2)

Nécessaire pour que les utilisateurs se connectent d'un clic dans WaseBoard plutôt que de
coller leur ID Discord à la main.

1. Sur la même application Discord (étape 1), onglet **OAuth2**.
2. Copiez le **Client Secret** (bouton "Reset Secret" si besoin) dans `config.json`
   (`oauth2_client_secret`). Le **Client ID** est déduit tout seul de votre bot ; ne renseignez
   `oauth2_client_id` que si vous voulez le forcer.
3. Section **Redirects**, ajoutez exactement (barre oblique finale incluse) :
   `http://127.0.0.1:48899/callback/`
   (le client WaseBoard héberge lui-même un petit serveur local le temps de la connexion —
   rien à exposer publiquement, ça fonctionne même sans nom de domaine).

## 4 bis. Vérifier la configuration

Avant (ou après) le premier lancement, une commande contrôle tout sans connecter le bot à Discord
(donc sans risque de doublon avec une instance déjà en marche) :

```bash
python3 server.py --check        # secret, adresse publique, ffmpeg/opus, dossier de données,
                                 # jeton, intent « Server Members », redirection OAuth2, joignabilité
python3 server.py --invite-url   # affiche l'URL pour inviter le bot (droits Voir/Se connecter/Parler)
```

Chaque ligne est un ✅, ⚠️ ou ❌ avec, si besoin, la correction à faire ; le code de sortie est ≠ 0
s'il y a au moins un ❌. Une fois le bot lancé, la commande **`/diagnostic`** (réservée aux
administrateurs du serveur Discord, réponse visible d'eux seuls) refait les mêmes contrôles et vérifie en
plus les droits du bot sur **chaque salon vocal** du serveur et sur le vôtre.

Toute la configuration peut aussi venir de variables d'environnement `WASEBOARD_*` (`BOT_TOKEN`,
`SHARED_SECRET`, `PUBLIC_URL`, `OAUTH2_CLIENT_SECRET`, `GUILD_ID`, `HTTP_PORT`...), qui l'emportent sur
`config.json` ; `WASEBOARD_DATA_DIR` déplace toutes les données (catalogue, sons, sessions...) hors du
dossier du script. Rien à changer pour une installation existante.

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
dans un salon Discord — ça poste un bouton persistant (survit aux redémarrages du bot). Vous pouvez
aussi taper `/panneau` dans un salon pour y poster trois boutons utilisables par tout le monde sans
commande : 🔊 rejoindre son vocal, ⏹️ couper les sons en cours, ❓ un rappel du fonctionnement — guide
complet pour vos membres : [`docs/FAQ-utilisateurs.md`](../docs/FAQ-utilisateurs.md).
Toute personne pouvant voir ce salon peut cliquer dessus pour recevoir, en message visible
d'elle seule, un **bouton « Ouvrir WaseBoard »** : il ouvre une petite page de votre serveur
(`<public_url>/connect/<code>`) qui lance l'application et pré-remplit automatiquement
l'adresse et le jeton d'accès (rien à copier-coller). Cette page propose aussi le téléchargement
de WaseBoard à qui ne l'a pas encore (`download_url` dans `config.json`, par défaut la dernière
version publiée sur GitHub) et un lien à coller dans Windows + R en dernier recours.

Pourquoi une page et pas directement le lien `waseboard://` : Discord n'affiche pas comme
cliquable un lien à schéma personnalisé, alors qu'un bouton https marche partout (ordinateur,
web, mobile). Le code de l'adresse est aléatoire, valable 15 minutes et gardé en mémoire
seulement ; **le jeton d'accès n'apparaît jamais dans l'URL**, seulement dans la page, une fois
le code validé. Cette route est publique par conception (elle s'ouvre depuis un navigateur,
sans en-tête d'authentification) mais ne révèle rien sans code valide. Qui voit le bouton Discord
se règle en restreignant l'accès au salon via les permissions Discord habituelles — rien à
configurer côté WaseBoard.

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

**Découpe non destructive.** Quand on découpe un son à l'ajout, le serveur garde le **fichier
complet** et mémorise seulement le début et la fin retenus (`trim_start_ms` / `trim_end_ms`) ;
`ffmpeg` ne joue que cette portion. L'auteur (ou un admin) peut donc recouper le son plus tard,
même après des semaines, sans le renvoyer. L'emoji se choisit aussi dès l'ajout. Les sons
ajoutés avant cette fonction restent tels quels (leur fichier était déjà découpé).

Les sons uploadés avant l'arrivée des rôles n'ont pas d'auteur enregistré : seuls les admins
peuvent les gérer. Un son supprimé va dans une **corbeille** (fichier conservé, partages
mémorisés) : un admin peut le restaurer pendant `trash_retention_days` jours.

**Panel d'administration** (Paramètres > Administration dans WaseBoard, visible des seuls admins) :
sons de la guilde (auteur, date, taille, nombre de lectures), corbeille, réglages (rôle admin,
upload réservé aux admins, quotas de nombre/taille/durée, anti-spam à la lecture, membres
bloqués), statistiques d'usage, journal d'actions, lien d'invitation. La limite de durée
nécessite `ffprobe` (fourni avec ffmpeg) ; s'il manque, elle est simplement ignorée.

**Fichiers de données créés à côté de `server.py` (ou dans `WASEBOARD_DATA_DIR`)** (à inclure dans vos sauvegardes avec
`sounds_data/` et `shared_categories.json`) : `guild_settings.json` (réglages par serveur),
`audit.jsonl` (journal d'actions), `stats.jsonl` (lectures), `sounds_data/trash/` +
`sounds_data/trash.json` (corbeille).

## Capacité et exploitation (pour qui héberge)

Tout ce qui suit est **facultatif et désactivé par défaut** : sans rien configurer, le serveur se comporte comme
avant. À activer quand plusieurs serveurs Discord partagent votre machine. Chaque réglage existe dans `config.json`
et sous forme de variable d'environnement (`WASEBOARD_` + le nom en majuscules, ex : `WASEBOARD_MAX_GUILDS`).

### Plafonds de ressources de l'instance (0 = désactivé)

| Réglage | Effet |
|---|---|
| `max_guilds` | nombre de serveurs Discord où le bot reste présent. Au-delà, le bot **quitte** le nouveau serveur après avoir prévenu son propriétaire par message privé. Les serveurs déjà présents ne sont jamais touchés. |
| `max_concurrent_voice` | salons vocaux occupés en même temps. Au-delà, `/join` et le bouton « Rejoindre mon vocal » répondent « serveur saturé, réessayez dans quelques minutes ». |
| `max_sources_per_guild` | sons **différents** joués en même temps sur un même serveur (rejouer un son déjà en cours le remplace : jamais refusé). |
| `max_ffmpeg_processes` | sons joués en même temps, tous serveurs confondus (un processus ffmpeg chacun). |
| `http_rate_limit_per_min` | requêtes par minute et par adresse IP sur les seules routes publiques ou coûteuses : `/connect/…`, `/oauth/…` (compteur commun) et l'envoi de sons. Les routes utilisées en continu par l'appli (`/activity`, `/status`, `/play`…) ne sont **jamais** freinées. Derrière nginx/Caddy, l'adresse du client est lue dans `X-Forwarded-For` (dernière entrée, celle vue par votre proxy). |

Un refus de lecture répond `429` avec un message lisible (« Trop de sons en même temps… ») et **ne compte pas** dans
l'anti-spam du membre.

### Réglages imposés aux serveurs Discord

Chaque serveur règle ses limites dans le panel d'administration (nombre de sons, taille d'un fichier, durée, anti-spam,
et **espace disque total** `max_total_mb`). Vous pouvez, en tant qu'hébergeur, imposer :

```json
{
  "default_guild_limits":  { "max_sounds": 300, "max_file_mb": 20, "max_total_mb": 500 },
  "guild_limit_ceilings":  { "max_file_mb": 50, "max_total_mb": 2000 }
}
```

- `default_guild_limits` : valeurs de départ des serveurs qui **n'ont encore rien enregistré** (leurs admins peuvent les
  modifier). Un serveur qui a déjà enregistré ses réglages n'est jamais modifié rétroactivement. Pour que le vôtre
  reste illimité, enregistrez une fois ses réglages dans le panel d'administration.
- `guild_limit_ceilings` : plafonds que les admins de serveurs **ne peuvent pas dépasser** (« 0 = illimité » n'est alors
  plus permis pour ce réglage ; le panel affiche la limite imposée et refuse une valeur au-dessus). Rien n'est supprimé :
  seuls les futurs ajouts sont refusés.
- Réglages concernés : `max_sounds`, `max_file_mb`, `max_duration_s`, `max_total_mb`, `play_rate_per_min`.

### Voir l'état de l'instance : `/instance`

Commande Discord réservée à la personne propriétaire de l'application (réponse visible d'elle seule) : durée de
fonctionnement, serveurs / salons vocaux / sons en lecture face aux plafonds, applications ouvertes, taille du catalogue,
espace disque libre, serveurs les plus lourds et plafonds actifs. Pour une sonde externe (UptimeRobot, Kuma…), utilisez
`GET /health` : `200` quand le bot est connecté à Discord, `503` sinon, sans aucun détail.

### Quelle capacité ? (mesures)

Mesure sans Discord, sur la machine de test (6 cœurs, 11 Go, partagée avec d'autres services) : de vrais ffmpeg, le vrai
mixeur et l'encodage Opus, une trame toutes les 20 ms par salon, retard de chaque trame relevé.

| Salons × sons simultanés | Processus ffmpeg | CPU | Mémoire | Trames en retard (> 40 ms) |
|---|---|---|---|---|
| 8 × 3 | 24 | 0,8 cœur | ≈ 330 Mo | 0 % |
| 16 × 3 | 48 | 1,5 cœur | ≈ 590 Mo | 0 % |
| 24 × 3 | 72 | 2,2 cœurs | ≈ 840 Mo | 0 % |
| 40 × 3 | 120 | 2,6 cœurs | ≈ 1,4 Go | 0 % |

Soit environ **0,02 à 0,03 cœur et 10 Mo par son joué simultanément**. En usage réel les sons sont courts et rarement
simultanés : la charge moyenne est très inférieure. Points de départ prudents pour cette machine, à ajuster avec
`/instance` : `max_concurrent_voice` 40, `max_ffmpeg_processes` 100, `max_sources_per_guild` 8, `max_guilds` 100,
`http_rate_limit_per_min` 60. Sur une autre machine, adaptez-les (le CPU décide : comptez ~0,03 cœur par son simultané,
en laissant la moitié des cœurs libre).

### Sauvegarde et restauration

`deploy/backup.sh` archive le catalogue, les sons (corbeille comprise), les partages, les réglages des serveurs, le
journal d'actions et les statistiques dans `~/waseboard-backups/waseboard-data-AAAAMMJJ-HHMMSS.tgz` (droits 600), vérifie que
l'archive se relit, puis supprime celles de plus de 7 jours. `config.json` et `oauth_sessions.json` (secrets) n'y sont
**pas** inclus, sauf avec `-s`. Sans danger pendant que le serveur tourne.

```bash
./deploy/backup.sh                       # une sauvegarde maintenant
./deploy/backup.sh -d ~/waseboard-server -o /mnt/disque/sauvegardes -k 14   # dossiers et durée au choix
```

Sauvegarde quotidienne automatique (systemd) : copiez `deploy/waseboard-backup.service` et `deploy/waseboard-backup.timer`
dans `/etc/systemd/system/`, remplacez `VOTRE_USER`, puis `sudo systemctl daemon-reload && sudo systemctl enable --now waseboard-backup.timer`
(`systemctl list-timers` pour vérifier, `journalctl -u waseboard-backup` pour le résultat).

**Restaurer** (serveur arrêté ; l'existant est mis de côté, pas écrasé) :

```bash
sudo systemctl stop waseboard
cd ~/waseboard-server            # le dossier de données
mkdir -p ~/avant-restauration && mv sounds_data shared_categories.json guild_settings.json audit.jsonl stats.jsonl ~/avant-restauration/ 2>/dev/null
tar xzf ~/waseboard-backups/waseboard-data-AAAAMMJJ-HHMMSS.tgz
sudo systemctl start waseboard
```

Avec Docker : la sauvegarde et la restauration du volume sont décrites plus haut (« Données et sauvegarde ») ;
restaurer = `docker compose stop waseboard`, puis `tar xzf` de l'archive dans le volume avec la même commande
`docker run … alpine`, puis `docker compose start waseboard`. Pour l'automatiser, une ligne de `crontab` suffit.

### Service systemd durci

`deploy/waseboard.service` remplace le service de l'étape 5 : le serveur ne peut écrire que dans son propre dossier
(`ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges`, `/tmp` privé), il est arrêté proprement (les salons
vocaux sont quittés) et borné en mémoire (`MemoryMax=2G`) et en processus (`TasksMax=512`) — à ajuster à votre machine.
Remplacez `VOTRE_USER` et les chemins, puis `sudo systemctl daemon-reload && sudo systemctl restart waseboard`.

## Sécurité

- Depuis l'ajout de la connexion Discord (OAuth2), l'accès réel au catalogue/aux sons repose
  sur une identité Discord vérifiée + l'appartenance à la bonne guilde — plus seulement sur
  `shared_secret`. Ce dernier protège désormais surtout les quelques routes utilisables avant
  toute connexion (`/status`, `/activity`, `/oauth/client-id`, `/oauth/exchange`), pour éviter
  qu'un inconnu sur internet puisse ne serait-ce que sonder le serveur ou saturer l'échange
  OAuth2 — choisissez-le quand même long et aléatoire. Aucune limitation de débit n'est active par
  défaut : voir `http_rate_limit_per_min` dans « Capacité et exploitation ».
- Le lien de connexion (`/configurer-invitation`) donne accès à ce `shared_secret` — qui
  voit le bouton Discord qui le distribue doit donc être contrôlé via les permissions du
  salon où vous le postez, pas seulement en gardant le secret pour vous.

## Dépannage

- **Première chose à essayer** : `python3 server.py --check`, puis `/diagnostic` dans Discord — ils repèrent la plupart des erreurs de configuration.
- **Le bot ne démarre pas (jeton refusé, « Server Members » désactivé)** : le message affiché dit où corriger dans le portail Discord.
- **Le bot ne rejoint pas / erreur PyNaCl** : `pip install -r requirements.txt --force-reinstall`.
- **Le son ne se joue pas** : vérifiez `ffmpeg -version` fonctionne sur le serveur.
- **Un client ne peut pas se connecter** : depuis une autre machine,
  `curl https://VOTRE_DOMAINE/status -H "X-WaseBoard-Token: VOTRE_SECRET"`.
- **"Address already in use"** : changez `http_port` dans `config.json`, ou arrêtez l'ancien
  processus (`sudo lsof -i :5005`).
