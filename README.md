# 🎛️ WaseBoard

Soundboard partagé pour Discord : cliquez un son, il joue à la fois sur vos enceintes et dans
votre salon vocal, pour tout le monde — sans jamais passer par votre micro (donc pas filtré par
les suppressions de bruit type Krisp).

**[⬇ Télécharger le client Windows](https://github.com/salsi64/WaseBoard/releases/latest)**

## Comment ça marche

```
[WaseBoard.exe] ──HTTP──► [Votre serveur] ──► Bot Discord (joue dans le vocal)
   (un par utilisateur)     (catalogue de sons partagé)
```

- **Le client** (ce que vous installez) : ajoute, liste et joue les sons.
- **Le serveur** : héberge le catalogue partagé et le bot Discord. Chaque groupe héberge le
  sien — voir [`server/README.md`](server/README.md) pour mettre en place le vôtre (gratuit,
  ~15 minutes). Si quelqu'un vous a invité sur son serveur, demandez-lui simplement l'adresse
  et le jeton d'accès : rien à installer côté serveur.

## Fonctionnalités

- **Aperçu local** (icône 📣) avant de jouer réellement dans le vocal ; le bouton s'illumine
  pour tout le monde pendant la lecture, avec l'avatar de qui joue.
- **Plusieurs sons en même temps**, sans s'annuler entre utilisateurs.
- **Favoris**, **catégories personnelles** et **catégorie automatique par serveur Discord**,
  glisser-déposer pour classer/réorganiser, recherche en temps réel.
- **Emoji obligatoire par son**, en couleur, palette prédéfinie ou personnalisée.
- **Découpe waveform** à l'ajout d'un son, pour choisir l'extrait exact.
- **Raccourcis clavier globaux**, thème clair/sombre et interface classique ou moderne
  (barre latérale), au choix.
- **Identification automatique** : votre compte Discord suffit, WaseBoard retrouve tout seul
  votre salon vocal — rien à choisir manuellement.
- Formats supportés : mp3, wav, ogg (Vorbis et Opus), flac, m4a, wma.

## Développer / héberger

### Serveur

Suivez [`server/README.md`](server/README.md) en premier (bot Discord, mise en place, HTTPS).
Le client ne fonctionne pas sans serveur joignable.

### Client

Pré-requis : [.NET 8 SDK](https://dotnet.microsoft.com/download) sur Windows.

```powershell
cd src/WaseBoard
dotnet build -c Release
```

Exécutable autonome (self-contained) :

```powershell
dotnet publish src/WaseBoard/WaseBoard.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true
```

Installeur : ouvrez `installer/setup.iss` avec [Inno Setup](https://jrsoftware.org/isinfo.php)
et compilez (F9) → l'installeur apparaît dans `installer/Output/`.

### Structure du projet

```
src/WaseBoard/    → Client Windows (WPF, .NET 8)
server/           → Serveur Python (catalogue de sons + bot Discord), voir son README
installer/        → Script Inno Setup pour générer l'installeur Windows
```

## Pistes d'amélioration

- Import/export de sélections de sons.
- Historique des sons les plus joués.
- Installeur plus léger (mode "framework-dependent", au prix de nécessiter le
  [.NET 8 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/8.0) sur chaque poste).
