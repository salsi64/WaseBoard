# 🎛️ WaseBoard

Soundboard partagé pour Discord : cliquez un son, il joue à la fois sur vos enceintes et dans
votre salon vocal, pour tout le monde — sans jamais passer par votre micro (donc pas filtré par
les suppressions de bruit type Krisp).

**[⬇ Télécharger le client Windows](https://github.com/salsi64/WaseBoard/releases/latest)** ·
**[💬 Rejoindre le Discord WaseBoard](https://discord.gg/HAGTNGFyQd)**

## Comment ça marche

WaseBoard est **auto-hébergé** : il n'y a pas de serveur central, chaque groupe fait tourner
le sien (gratuit, ~15 minutes) — il héberge le catalogue de sons partagé et le bot Discord qui
les joue dans le vocal.

## Installation / utilisation

Si quelqu'un dans votre groupe a déjà un serveur WaseBoard (le bot est déjà présent sur votre
Discord) :

1. Cliquez sur le bouton de connexion reçu sur Discord (📬 **Recevoir mon lien WaseBoard**, posté
   par un admin) : WaseBoard s'installe et se configure tout seul. Sans ce bouton, [téléchargez et
   installez le client](https://github.com/salsi64/WaseBoard/releases/latest), puis saisissez
   l'adresse du serveur et le jeton d'accès donnés par cette personne, dans les Paramètres.
2. Connectez-vous avec votre compte Discord quand l'application le demande — ça sert uniquement à
   afficher votre avatar et à retrouver votre salon vocal, rien d'autre à saisir.
3. Rejoignez un salon vocal, cliquez 🔊 **Rejoindre mon vocal** dans WaseBoard.
4. Cliquez un son — c'est prêt.

Guide complet et FAQ : [`docs/FAQ-utilisateurs.md`](docs/FAQ-utilisateurs.md).

## Fonctionnalités

- **Aperçu local** (émoticône sur la gauche des boutons) avant de jouer réellement dans le vocal ; le bouton s'illumine
  pour tout le monde pendant la lecture, avec l'avatar de qui joue.
- **Plusieurs sons en même temps**, sans s'annuler entre utilisateurs.
- **Favoris**, **catégories personnelles** et **catégorie automatique par serveur Discord**,
  glisser-déposer pour classer/réorganiser, recherche en temps réel.
- **Emoji obligatoire par son**, en 3D brillante (Fluent Emoji), palette prédéfinie ou personnalisée.
- **Découpe waveform** non destructive : le son complet est conservé, on peut recouper plus tard
  (clic droit › Redécouper), et choisir l'emoji dans la même fenêtre.
- **Raccourcis clavier globaux**, thème clair/sombre et interface classique ou moderne
  (barre latérale), au choix. La forme d'onde des boutons est optionnelle : masquée, les boutons
  tiennent sur une seule ligne et l'avatar de la personne qui joue reste affiché à droite du nom.
- **Identification automatique** (connexion Discord) : votre compte suffit, WaseBoard retrouve
  tout seul votre salon vocal — rien à choisir manuellement.
- **Plusieurs serveurs Discord** sur une même instance, catalogues isolés par serveur ; les sons
  d'un serveur où vous êtes aussi membre restent jouables partout où vous allez.
- **Panel d'administration** par serveur (rôles, corbeille avec restauration, statistiques,
  journal d'actions, quotas, anti-spam) et un **panneau de boutons Discord** (`/panneau`) pour
  rejoindre le vocal ou couper les sons sans quitter Discord.
- Formats supportés : mp3, wav, ogg (Vorbis et Opus), flac, m4a, wma.

## Héberger

Personne dans votre groupe n'a encore de serveur ? Un assistant Docker s'occupe de tout (bot
Discord, HTTPS automatique) — gratuit, ~15 minutes. Suivez
[`server/README.md`](server/README.md).

## Pistes d'amélioration

- Import/export de sélections de sons.
- Client multi-serveurs (se connecter à plusieurs instances WaseBoard indépendantes à la fois).
- Installeur plus léger (mode "framework-dependent", au prix de nécessiter le
  [.NET 8 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/8.0) sur chaque poste).

## Crédits

- **Emojis** : [Fluent Emoji](https://github.com/microsoft/fluentui-emoji) (Microsoft, licence MIT), affichés
  depuis le dépôt via jsDelivr — les rares emojis absents de ce jeu (certaines variantes de teinte de peau, 🅾️...)
  retombent sur [Twemoji](https://github.com/jdecked/twemoji) (graphismes CC-BY 4.0).
