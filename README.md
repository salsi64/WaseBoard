# 🎛️ WaseBoard

Soundboard partagé pour Discord : cliquez un son, il joue à la fois sur vos enceintes et dans
votre salon vocal, pour tout le monde — sans jamais passer par votre micro (donc pas filtré par
les suppressions de bruit type Krisp).

**[⬇ Télécharger le client Windows](https://github.com/salsi64/WaseBoard/releases/latest)**

## Comment ça marche

WaseBoard est **auto-hébergé** : il n'y a pas de serveur central, chaque groupe fait tourner
le sien (gratuit, ~15 minutes) — il héberge le catalogue de sons partagé et le bot Discord qui
les joue dans le vocal.

## Installation / utilisation

Si quelqu'un dans votre groupe a déjà un serveur WaseBoard (le bot est déjà présent sur votre
Discord) :

1. [Téléchargez et installez le client](https://github.com/salsi64/WaseBoard/releases/latest).
2. Demandez à cette personne l'**adresse du serveur** et le **jeton d'accès**, à saisir au
   premier lancement (ou plus tard dans les Paramètres).
3. Cliquez sur le bouton: Rejoindre mon vocal.   
4. Cliquez un son — c'est prêt.

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
  (barre latérale), au choix.
- **Identification automatique** : votre compte Discord suffit, WaseBoard retrouve tout seul
  votre salon vocal — rien à choisir manuellement.
- Formats supportés : mp3, wav, ogg (Vorbis et Opus), flac, m4a, wma.

## Héberger

Personne dans votre groupe n'a encore de serveur ? Suivez
[`server/README.md`](server/README.md) (bot Discord, mise en place, HTTPS) — gratuit,
~15 minutes.

## Pistes d'amélioration

- Import/export de sélections de sons.
- Historique des sons les plus joués.
- Installeur plus léger (mode "framework-dependent", au prix de nécessiter le
  [.NET 8 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/8.0) sur chaque poste).

## Crédits

- **Emojis** : [Fluent Emoji](https://github.com/microsoft/fluentui-emoji) (Microsoft, licence MIT), affichés
  depuis le dépôt via jsDelivr — les rares emojis absents de ce jeu (certaines variantes de teinte de peau, 🅾️...)
  retombent sur [Twemoji](https://github.com/jdecked/twemoji) (graphismes CC-BY 4.0).
