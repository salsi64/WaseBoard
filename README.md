# WaseBoard

Application Windows de soundboard partagée : cliquez un son, il est joué à la fois sur
vos propres enceintes (retour immédiat) et par un bot dans votre salon vocal Discord —
sans jamais passer par votre micro, donc sans être filtré par les suppressions de bruit IA
(Krisp). Les sons sont stockés sur un serveur central, partagés entre tous les utilisateurs.

## Architecture

```
[WaseBoard.exe] ──HTTP──► [Serveur central] ──► Bot Discord (mixe et joue dans le(s) vocal(aux))
   (par utilisateur)         (catalogue de
                              sons partagé)
```

- **Client (`src/`)** : l'application Windows — ajoute des sons (avec découpe waveform),
  les liste, les joue. Aucun stockage local des fichiers audio eux-mêmes : un cache local
  sert uniquement au retour audio sur vos propres enceintes.
- **Serveur (`server/`)** : héberge le catalogue de sons partagé et le bot Discord. Voir
  `server/README.md` pour la mise en place complète. Le bot peut être connecté à
  **plusieurs serveurs Discord différents en même temps**.

## Fonctionnalités côté client

- Récupère la liste des sons depuis le serveur au démarrage ; bouton "⟳ Actualiser" pour
  resynchroniser à tout moment.
- Ajout d'un son (bouton "+" ou glisser-déposer) avec éditeur **waveform** pour choisir un
  extrait avant envoi au serveur.
- **Aperçu** : l'icône 📣 (mégaphone) à gauche de chaque bouton joue le son **localement
  uniquement**, sans le déclencher côté Discord. Seule cette icône se met en valeur pendant
  l'aperçu — le bouton principal, lui, s'illumine pour **tout le monde** dès que quelqu'un
  (vous ou un autre utilisateur) joue réellement ce son dans le vocal, avec son avatar Discord
  affiché sur le bouton.
- **Plus besoin de choisir un serveur Discord** : chaque clic est identifié par votre compte
  Discord (choisi une fois dans les Paramètres), et le serveur retrouve tout seul le bon salon
  vocal à partir de votre présence dedans.
- **Recherche** : champ 🔍 en haut de la page, filtre toutes les sections par nom de son en
  temps réel.
- **Emoji** par son : palette d'emojis prédéfinis directement dans l'app (clic droit > Choisir
  un emoji), plus un champ pour coller un emoji personnalisé — préférence locale.
- **Stop tout** coupe désormais réellement tous les sons en cours de lecture sur Discord (pas
  seulement le retour local), dans le salon vocal où vous êtes.
- **Plusieurs utilisateurs peuvent déclencher des sons en même temps sans s'annuler** : le
  serveur mixe les sons ensemble au lieu de remplacer ce qui est en cours.
- Formats supportés : mp3, wav, ogg (Vorbis **et** Opus, détectés et décodés automatiquement),
  flac, m4a, wma.
- **Tous les sons** en bas de page liste systématiquement l'intégralité du catalogue — les
  catégories sont un classement additionnel, jamais exclusif.
- **Favoris** et **catégories personnelles** : sections repliables sur la page principale,
  glisser-déposer pour classer/réorganiser. Préférences locales à chaque utilisateur.
- **Catégorie partagée automatique par serveur Discord** : une catégorie apparaît toute seule
  pour chaque Discord dont vous êtes membre (identifié par votre ID Discord dans les
  Paramètres) — rien à créer ni nommer. Visible et modifiable par tous les membres du même
  serveur, avec une barre d'avatars des utilisateurs ayant WaseBoard ouvert en ce moment
  affichée dans l'en-tête.
- **Identité Discord** : collez simplement votre ID Discord dans les Paramètres (Discord >
  Paramètres > Avancés > Mode développeur, puis clic droit sur votre profil > Copier l'ID).
- Couleur de fond personnalisable (Paramètres > Apparence), appliquée immédiatement.
- **Thème d'interface** (Paramètres > Apparence) : "Classique" (barre d'outils, boutons en
  carte, comportement historique) ou "Moderne" (barre latérale rétractable, boutons en pilule
  colorée, indicateur de présence vocale en direct). Basculer entre les deux ne perd aucune
  donnée — c'est purement visuel, changeable à tout moment.
- **Barre latérale** (thème moderne) : rétractable (bouton «/») pour gagner de la place, liste
  dynamiquement **toutes** les sections (favoris, chaque catégorie perso/partagée, tous les
  sons) et non plus seulement des raccourcis fixes. L'indicateur de présence reflète votre
  **statut vocal réel en direct** (plus jamais "connecté" par erreur une fois sorti du vocal,
  ni le mauvais serveur Discord affiché).
- **Réorganisation des catégories par glisser-déposer** : plus de flèches ↑/↓, on fait
  glisser l'en-tête d'une catégorie personnelle sur une autre pour la repositionner.
- **Animation de glisser-déposer** : le bouton déplacé a un léger balancement (façon icônes
  iOS en réorganisation), et le bouton actuellement survolé s'illumine pour indiquer où le
  son atterrira.
- Emojis en couleur (police système dédiée), palette élargie dans le sélecteur.
- **"🔊 Rejoindre mon vocal"** : fait rejoindre le bot au salon vocal où vous êtes actuellement,
  directement depuis WaseBoard — plus besoin de taper `/join` dans Discord.
- **Pas de chevauchement** : rejouer un son déjà en train de jouer le relance depuis le début
  au lieu de superposer une deuxième instance par-dessus la première.
- **Retirer un son d'une catégorie** : clic droit sur un son affiché dans une catégorie
  (personnelle ou partagée) > "Retirer de « nom »" — sans le supprimer du catalogue.
- Barre latérale : icônes réelles des serveurs Discord (au lieu d'un simple 🌐 générique)
  pour les catégories partagées, et réorganisation des catégories personnelles directement
  depuis cette liste par glisser-déposer.
- Raccourcis clavier globaux (avec possibilité de les retirer), effet lumineux de contour
  partagé et synchronisé sur la durée réelle du son, icône et interface personnalisables.

## Mise en place

1. **Serveur** : suivez `server/README.md` en premier (bot Discord, port, lancement). Le
   client ne fonctionne pas sans serveur joignable.
2. **Client** : voir ci-dessous pour builder l'exécutable Windows.

## Build du client

Pré-requis : [.NET 8 SDK](https://dotnet.microsoft.com/download) sur une machine Windows.

```powershell
cd src/WaseBoard
dotnet restore
dotnet build -c Release
```

## Publication (exécutable autonome)

```powershell
dotnet publish src/WaseBoard/WaseBoard.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true
```

## Signer l'exécutable (éviter l'alerte SmartScreen)

1. Achetez un certificat de signature de code (OV, ou EV pour une réputation immédiate).
2. `signtool sign /f "moncert.pfx" /p "motdepasse" /fd sha256 /tr http://timestamp.digicert.com /td sha256 chemin\vers\WaseBoard.exe`
3. Signez aussi l'installeur final (voir section suivante).

## Créer l'installeur final (.exe)

1. Installez [Inno Setup](https://jrsoftware.org/isinfo.php).
2. Ouvrez `installer/setup.iss`, compilez (F9) → l'installeur apparaît dans `installer/Output/`.

## Structure du projet

```
WaseBoard.sln
src/WaseBoard/
├── Models/             → SoundItem, AppSettings
├── Services/           → SoundLibraryService (client HTTP + cache + favoris/catégories/ordre),
│                          AudioPlaybackService (lecture NAudio locale),
│                          AudioReaderFactory (choix du décodeur, dont l'ogg via NAudio.Vorbis),
│                          AudioTrimService (waveform + découpe précise),
│                          GlobalHotkeyManager
├── Windows/            → SettingsWindow, TrimWindow, PromptDialog, HotkeyCaptureWindow
├── MainWindow.xaml(.cs)→ Fenêtre principale
└── App.xaml(.cs)
server/                 → Serveur Python (catalogue de sons + bot Discord multi-guild), voir son README
installer/setup.iss     → Script Inno Setup (client Windows uniquement)
```

## Notes techniques

- **Découpe audio précise** : export par lecture strictement séquentielle depuis le début du
  fichier (jamais de "seek"), et calcul de la waveform par tranches de temps **strictement
  égales** (division flottante, pas tronquée). L'aperçu de l'éditeur réutilise le même export,
  pour être toujours fidèle au résultat final. Le calcul de waveform détecte aussi le format
  réel des échantillons (flottant 32 bits pour wav/mp3/flac/Vorbis, PCM 16 bits pour l'Opus
  décodé via Concentus) — une hypothèse figée sur un seul format cassait l'aperçu des fichiers
  Opus.
- **Superposition des sons** : côté serveur, un mixeur audio persistant par salon vocal
  combine tous les sons déclenchés en même temps au lieu de les remplacer — indispensable
  dès que plusieurs utilisateurs partagent le même serveur.
- **Highlight synchronisé sur la vraie durée** : le serveur efface le highlight partagé
  précisément quand un son a fini de jouer (callback du mixeur), pas après un délai fixe
  approximatif. Le sondage côté client est également passé d'1s à 300ms (avec un sondage
  immédiat après votre propre clic), pour éliminer le délai/flicker perceptible sur les sons
  courts qu'un sondage plus lent provoquait.
- **Double thème** : le thème moderne réutilise exactement les mêmes données et la même
  logique que le classique — seul l'habillage visuel change (`ThemeState.IsModern` +
  `SoundButtonTemplateSelector`), pour permettre un retour en arrière sans risque.
- **Résolution des membres Discord** : robuste même si un utilisateur n'apparaît pas dans le
  cache du bot au moment de la liste — un appel `fetch_member` de secours est tenté avant
  d'abandonner. En dernier recours, un utilisateur peut toujours coller son ID Discord
  manuellement dans les Paramètres.
- **Catégorie partagée automatique** : stockée côté serveur (`shared_categories.json`, une
  simple liste de sons par `guild_id`), distincte des catégories 100% personnelles de chaque
  utilisateur (stockées localement). Son existence découle directement de l'appartenance de
  l'utilisateur au serveur Discord (`GET /my-guilds`) — pas de création/nommage manuel.
- **Mixeur indexé par clé** : chaque son ajouté au mixeur d'un salon est désormais indexé par
  son `sound_id` plutôt que stocké dans une simple liste — rejouer un son déjà en cours
  remplace immédiatement l'ancienne instance (avec son callback `on_finish`, donc le
  highlight de qui l'a lancé précédemment s'efface correctement) au lieu de la superposer.
- **Piège des clés de repli incohérentes** : l'état "replié/déplié" d'une section est stocké
  sous la clé de l'`Expander.Tag`, qui doit être EXACTEMENT la même chaîne utilisée pour
  vérifier cet état au rechargement — une catégorie partagée dont la clé de vérification était
  préfixée différemment de sa clé de sauvegarde ne retrouvait jamais son état.
- **Krisp / suppression de bruit Discord** : le son passe par le bot (participant séparé du
  vocal), donc jamais traité par les suppressions de bruit IA appliquées à un micro.
- **Statut vocal fiable** : le serveur avait un raccourci ("s'il n'y a qu'un seul salon
  connecté, c'est sûrement le bon") utile pour `/play`/`/stop` mais trompeur pour un indicateur
  de présence — il affichait "connecté" même quand l'utilisateur avait quitté le vocal, ou le
  mauvais serveur s'il était ailleurs. Un mode strict (`/status?strict=1`) ignore ce raccourci
  et ne vérifie que la présence vocale réelle, sondée en continu (3s).
- **Réorganisation animée** : un panneau personnalisé (`AnimatedWrapPanel`, technique FLIP —
  capture de la position avant/après, animation de la différence) anime le déplacement des
  boutons quand l'ordre change. Le réordonnancement se fait **en direct pendant le survol**
  (pas seulement au dépôt), pour que les autres boutons s'écartent visiblement pendant qu'on
  glisse plutôt que de sauter brutalement une fois relâché. Nécessite que les sections
  utilisent des `ObservableCollection` (déplacement in-place) plutôt que d'être reconstruites
  à chaque rafraîchissement, sans quoi WPF régénère les conteneurs et casse l'animation.
- **Présence vocale dans la barre latérale** : une bande toujours visible (même barre réduite)
  liste les avatars des personnes actuellement dans le même salon vocal que vous
  (`GET /status?strict=1` renvoie désormais aussi `channel_members`). Leur avatar s'illumine
  en même temps que leur bouton, croisé avec le sondage d'activité existant.

## Pistes d'amélioration possibles

- HTTPS entre client et serveur (actuellement en clair, voir "Sécurité" dans
  `server/README.md`).
- Import/export de sélections de sons.
- Historique des sons les plus joués.
- **Taille de l'installeur** : le client est actuellement publié en mode "self-contained"
  (le runtime .NET est embarqué, ~60-100 Mo). Pour un installeur nettement plus léger,
  publier en mode "framework-dependent" (`--self-contained false`) est possible, mais impose
  alors à chaque utilisateur d'avoir le
  [.NET 8 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/8.0) déjà installé —
  un compromis à évaluer selon votre public.
