# Clip des dernières secondes

Un moment très drôle vient de passer dans un jeu, une vidéo ou à l'oral dans Discord ? D'un raccourci, WaseBoard le
transforme en nouveau son : il garde en mémoire les dernières secondes de ce que joue votre PC, puis ouvre la fenêtre
d'ajout pour que vous le découpiez, le nommiez et l'envoyiez.

## Activer

**Paramètres › Clip** :

1. Cochez **« Garder en mémoire les dernières secondes du son de mon PC »** (désactivé par défaut).
2. Choisissez la durée gardée : **10, 15 ou 30 secondes**.
3. Cliquez sur **Définir…** pour choisir un raccourci clavier (par exemple `Ctrl+Alt+C`), puis **Enregistrer**.

Un bouton **Clip** (ciseaux, avec un point rouge) apparaît alors dans la barre d'outils. Le point rouge est le voyant « la capture
tourne » : il reste allumé tant que WaseBoard garde les dernières secondes en mémoire.

## S'en servir

Appuyez sur le raccourci (ou cliquez sur le bouton **Clip**) juste après le moment à garder :

1. un petit signal sonore confirme que le clip est gardé (décochable) — utile en plein jeu, où rien ne s'affiche à l'écran ;
2. la fenêtre d'ajout s'ouvre sur le clip, nommé `Clip 18:20` (l'heure) ;
3. faites glisser les poignées pour ne garder que la bonne partie, changez le nom si vous voulez, puis **Ajouter le son**.

Le son complet est conservé sur le serveur : vous pourrez recouper plus tard (clic droit › Éditer), comme pour n'importe
quel son.

Si rien n'a été joué sur le PC pendant la durée gardée, WaseBoard le dit au lieu d'ouvrir un clip vide.

## Ce qui est capturé, et ce qui ne l'est pas

- **Capturé** : tout ce que le PC joue sur sa sortie audio par défaut — le jeu, la musique, les vidéos, et **ce que disent
  les autres dans Discord** (puisque vous les entendez). N'ajoutez que ce que vous avez le droit de partager.
- **Pas capturé** : **votre micro**, et le son des applications qui jouent sur un périphérique autre que la sortie par défaut.
- Les enceintes 5.1 / 7.1 sont ramenées à deux voies (stéréo) ; le centre, où se trouvent les dialogues des jeux, est conservé.
- Si vous changez de sortie audio par défaut (casque branché, par exemple), la capture repart toute seule sur la nouvelle.
- Certains contenus protégés (DRM) peuvent être joués sans pouvoir être capturés : le clip est alors silencieux.

## Vie privée

- Tout reste **dans la mémoire vive** de votre ordinateur : rien n'est écrit sur le disque, rien n'est envoyé au serveur ni
  à personne **tant que vous n'avez pas demandé un clip puis ajouté le son**.
- Quand vous demandez un clip, il est écrit dans un fichier temporaire, **supprimé dès que la fenêtre d'ajout se ferme**
  (ajouté ou annulé).
- La capture s'arrête dès que vous décochez la case ou quittez WaseBoard.

## Si ça ne marche pas

- **Pas de bouton Clip** : la fonction n'est pas cochée dans Paramètres › Clip.
- **Pas de point rouge** : la capture ne démarre pas (passez la souris sur le bouton pour lire la raison). WaseBoard
  réessaie tout seul ; si votre PC n'a aucune sortie audio active, rien ne peut être capturé.
- **« Le raccourci … est déjà pris par une autre application »** : choisissez-en un autre ; le bouton Clip marche quand même.
- **Le clip est coupé trop court ou trop long** : la durée gardée se règle dans Paramètres › Clip ; vous coupez ensuite
  précisément dans la fenêtre d'ajout.
