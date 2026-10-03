# WaseBoard — Guide et FAQ pour les membres du serveur

Ce guide s'adresse à vous, membre d'un serveur Discord où WaseBoard est déjà installé. Pour héberger
votre propre serveur WaseBoard, voir [`server/README.md`](../server/README.md).

## En deux phrases

WaseBoard est un soundboard partagé : quand vous cliquez un son dans l'application, il joue à la fois
sur vos enceintes **et** dans le salon vocal Discord où vous êtes, pour tout le monde — sans passer par
votre micro (donc jamais coupé par un filtre anti-bruit type Krisp).

## Démarrer (une seule fois)

1. **[Téléchargez le client](https://github.com/salsi64/WaseBoard/releases/latest)** et installez-le
   (Windows).
2. **Récupérez votre lien de connexion** : dans Discord, cliquez sur le bouton **📬 Recevoir mon lien
   WaseBoard** posté par un admin (ou tapez la commande s'il n'y en a pas : demandez-lui de taper
   `/configurer-invitation`). Vous recevez un message visible de vous seul avec un bouton
   **🚀 Ouvrir WaseBoard** : cliquez dessus, l'application s'ouvre et se connecte toute seule.
3. **Connectez-vous avec Discord** quand l'application vous le demande (bouton bleu « Se connecter avec
   Discord ») — ça sert uniquement à afficher votre avatar quand vous jouez un son et à retrouver
   automatiquement le bon salon vocal.

Vous n'avez rien d'autre à installer ni à configurer : WaseBoard reconnaît votre serveur, vos sons et
votre salon vocal tout seul.

## Jouer un son

1. Rejoignez un salon vocal sur Discord, normalement.
2. Dans WaseBoard, cliquez **🔊 Rejoindre mon vocal** (une fois par session — le bot Discord doit être
   dans le même salon que vous).
3. Cliquez un son : il joue chez tout le monde dans ce salon, et le bouton s'illumine le temps de la
   lecture avec votre avatar dessus.

Vous pouvez aussi cliquer plusieurs sons d'affilée : ils se superposent, aucun n'annule les autres.
L'icône 📣 à gauche d'un bouton fait un **aperçu local** (chez vous seulement, sans déranger les autres)
— pratique pour vérifier un son avant de l'envoyer en vocal.

## Ajouter un son

« + Ajouter un son » (ou glisser-déposer un fichier dans la fenêtre) : donnez-lui un nom, un emoji, et
découpez si besoin la partie à garder. Le fichier d'origine reste toujours intact : vous (ou un admin)
pourrez recouper différemment plus tard sans le renvoyer (clic droit sur le son › *Redécouper*).

## FAQ

**WaseBoard doit-il rester ouvert pour que les sons jouent ?**
Seule la personne qui *clique* le son doit avoir l'application ouverte et connectée. Tout le monde
l'entend dans le vocal, même sans WaseBoard installé.

**Pourquoi je n'entends rien quand je clique un son ?**
Le bot Discord doit être connecté **au même salon vocal que vous** : cliquez d'abord
« 🔊 Rejoindre mon vocal » dans WaseBoard, ou tapez `/join` dans Discord une fois connecté en vocal.
S'il ne vous reste qu'un message d'erreur en bas de l'écran, lisez-le : il indique en général quoi faire.

**Un son que j'ai ajouté a disparu.**
Un admin l'a peut-être supprimé — c'est réversible pendant un moment (corbeille), demandez-lui. Sinon,
vérifiez que vous regardez le bon serveur si vous en avez plusieurs dans la barre latérale.

**Je ne peux plus ajouter de sons.**
Soit l'ajout est réservé aux admins sur ce serveur, soit un admin vous l'a retiré spécifiquement — vous
gardez le droit de jouer et de gérer vos favoris/catégories dans tous les cas. Voyez avec un admin.

**Puis-je jouer un son d'un autre serveur Discord que je fréquente ?**
Oui, si WaseBoard y est aussi installé et que vous en êtes membre : ses sons apparaissent dans votre
catalogue, jouables dans n'importe quel vocal où vous êtes.

**Est-ce que ça marche sur mobile / Mac / Linux ?**
Le client est pour l'instant Windows uniquement. Le bot Discord, lui, joue dans le vocal quel que soit
l'appareil des autres membres : eux n'ont besoin de rien installer pour *entendre*.

**Mes données sont-elles partagées avec un tiers ?**
Non : votre serveur WaseBoard est hébergé par un membre de votre groupe (pas par les auteurs du
logiciel), et votre connexion Discord ne sert qu'à vérifier votre identité et afficher votre avatar —
rien n'est transmis en dehors de ce serveur.

**Le bot a quitté le vocal tout seul.**
Normal : il part automatiquement dès que le salon est vide (plus personne, ou plus que des bots).

**J'ai cliqué le bouton « Ouvrir WaseBoard » et rien ne s'est passé.**
Le lien expire au bout de 15 minutes — redemandez-en un avec le bouton 📬. Si ça ne marche toujours
pas, le message propose aussi un lien à copier-coller dans la fenêtre *Exécuter* de Windows (touches
Windows + R).

**Qui peut voir mon pseudo/avatar dans WaseBoard ?**
Seuls les autres membres de vos serveurs Discord communs, exactement comme sur Discord — quand vous
jouez un son, votre avatar s'affiche sur le bouton le temps de la lecture (visible de tous ceux qui
jouent aussi ce vocal/serveur), et les admins voient votre nom dans les statistiques d'usage.

## Les boutons Discord

Si votre serveur a un panneau WaseBoard posté dans un salon (bouton **/panneau**, à la discrétion des
admins), trois boutons sont disponibles sans rien installer de plus que Discord :

| Bouton | Effet |
|---|---|
| 🔊 **Rejoindre mon vocal** | fait venir le bot WaseBoard dans votre salon vocal actuel |
| ⏹️ **Stop** | coupe tous les sons en cours sur ce serveur |
| ❓ **Aide** | rappelle l'essentiel de ce guide, en message privé (visible de vous seul) |

Commandes équivalentes à taper dans Discord : `/join`, `/leave` (déconnecte complètement le bot).

## Besoin d'aide ?

Demandez à un admin de votre serveur, ou passez sur le
[Discord WaseBoard](https://discord.gg/HAGTNGFyQd) — la communauté du projet, pas liée à un serveur
en particulier.
