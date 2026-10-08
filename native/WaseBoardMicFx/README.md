# WaseBoardMicFx : les sons du soundboard dans le vrai micro

Effet audio Windows (APO, *Audio Processing Object*) qui **ajoute les sons de WaseBoard au signal
du micro**. Tous les programmes qui écoutent ce micro (vocal d'un jeu, Discord, OBS…) entendent la
voix et les sons, sans logiciel tiers et sans rien changer dans le jeu.

Ce n'est **pas un pilote** : c'est une DLL en mode utilisateur que le moteur audio de Windows
(`audiodg.exe`) charge pour appliquer un effet au son d'un périphérique, comme Equalizer APO. Elle
n'a donc pas besoin de la signature de pilote de Microsoft (certificat EV).

## Fonctionnement

```
Vrai micro ──► pilote du micro (inchangé) ──► audiodg.exe ──► effets du fabricant ──► WaseBoardMicFx ──► jeu, Discord…
                                                                                         ▲
                                                Global\WaseBoardMicFeed (48 kHz mono)    │
                                                                                         │
                         WaseBoard.exe : décode et mixe les sons (Services/MicFx/MicFeedService.cs)
```

- **Où.** L'effet est un *effet d'endpoint* (EFX), ajouté **à la fin** de la liste composite du
  micro (`PKEY_CompositeFX_EndpointEffectClsid`, Windows 10 1803 et plus). Il s'applique à tous
  les flux du micro, y compris en mode RAW, après les effets du fabricant, et ne remplace rien.
- **Transport.** Une mémoire partagée contient une ligne de temps 48 kHz mono float32 :
  - WaseBoard y écrit le mélange des sons avec environ 60 ms d'avance ;
  - chaque instance de l'effet la lit à son propre rythme, avec un curseur à elle (un par micro
    équipé) ;
  - un micro qui ne tourne pas à 48 kHz est géré par interpolation linéaire.

  Le protocole est décrit dans [`src/MicFeedProtocol.h`](src/MicFeedProtocol.h), avec son miroir C#
  dans `src/WaseBoard/Services/MicFx/MicFeedRing.cs`.
- **Qui crée la mémoire.** C'est l'effet (session 0). Un programme utilisateur n'a pas le droit de
  créer un objet `Global\`, seulement d'en ouvrir un existant. WaseBoard réessaie donc de l'ouvrir
  tant qu'aucune application n'écoute un micro équipé.
- **Sans WaseBoard, rien ne change.** Le micro passe tel quel. La voix ne transite jamais par
  WaseBoard, qui peut être fermé sans couper le micro.
- **Ne jamais échouer.** Windows compte les échecs de chargement d'un effet. Au 10e, il désactive
  *tous* les effets du micro. Mémoire partagée absente, ou de version différente : l'effet devient
  transparent, il ne renvoie pas d'erreur.

## Installation (faite par WaseBoard)

Paramètres > **Micro en jeu** > *Installer*. WaseBoard se relance une fois en administrateur
(`WaseBoard.exe --micfx install …`, voir `Services/MicFx/MicFxSetup.cs`) et :

1. copie la DLL dans `C:\Program Files\WaseBoard\MicFx\WaseBoardMicFx-<empreinte>.dll`.
   Jamais dans le profil utilisateur : audiodg tourne en LOCAL SERVICE, et un dossier modifiable
   sans droits admin permettrait d'injecter du code dans le moteur audio ;
2. inscrit la classe COM et l'APO (`HKLM\SOFTWARE\Classes\CLSID\{8C9DCFA9-…}` et
   `…\AudioEngine\AudioProcessingObjects\{8C9DCFA9-…}`) ;
3. pour chaque micro choisi, sous `HKLM\…\MMDevices\Audio\Capture\{micro}\FxProperties` :
   - ajoute le CLSID en fin de `{d04e05a6-…},15` ;
   - déclare le mode `DEFAULT` en `{d3993a3f-…},7` s'il manque ;
   - réactive les « améliorations audio » si elles étaient coupées.

   L'état d'origine est noté sous `HKLM\SOFTWARE\WaseBoard\MicFx` ;
4. redémarre le service « Générateur de points de terminaison audio » (environ 2 s de coupure du
   son) pour appliquer la nouvelle liste sans redémarrer Windows.

*Désinstaller* défait exactement ces changements. Une mise à jour du pilote du micro efface souvent
sa liste d'effets : WaseBoard le détecte au démarrage et propose *Réparer*.

## Compiler

Il faut Visual Studio 2022 ou les Build Tools (charge de travail C++) et CMake :

```powershell
cmake -S native/WaseBoardMicFx -B native/WaseBoardMicFx/build -A x64
cmake --build native/WaseBoardMicFx/build --config Release
```

La DLL est copiée dans `native/WaseBoardMicFx/dist/`. `src/WaseBoard/WaseBoard.csproj` l'embarque
alors à côté de `WaseBoard.exe`.

**Sans Visual Studio**, le workflow GitHub `micfx-build.yml` compile et teste la DLL à chaque
modification de ce dossier. Pour récupérer la dernière DLL compilée de la branche courante :

```powershell
.\native\WaseBoardMicFx\fetch-dll.ps1
```

Elle a été compilée avec le CRT statique, sans manifeste embarqué. Elle ne dépend que de
`kernel32`, `ole32` et `advapi32`, et ne demande aucun redistribuable Visual C++.

## Tester

- **Automatique** : `tests/ApoTest.cpp` (lancé par la CI, ou à la main dans une console
  administrateur) charge la DLL et joue à la fois le rôle du moteur audio et celui de WaseBoard. Il
  vérifie :
  - la négociation de format et le passage du micro tel quel sans son ;
  - le mélange et les blocs « silencieux » ;
  - l'écrêtage doux et le recalage après un retard ;
  - la lecture d'un micro à 44,1 kHz.
- **En vrai** :
  1. installer l'effet depuis WaseBoard (Paramètres > Micro en jeu) ;
  2. ouvrir l'**Enregistreur vocal** de Windows et lancer un enregistrement ;
  3. cliquer *Tester (bip dans le micro)*, puis jouer un son ;
  4. réécouter l'enregistrement : on doit entendre la voix, le bip et le son.

  La ligne d'état sous les boutons indique si une application écoute le micro et si l'effet est
  bien chargé.

## Limites connues

- **Pas de périphérique séparé.** Les sons partent dans le micro pour **toutes** les applications.
  C'est pourquoi l'option « Jouer aussi dans Discord via le bot » est désactivée par défaut.
- **Annulation d'écho ou suppression de bruit de l'application.** Celles de Discord (Krisp) ou d'un
  jeu peuvent atténuer un son qui est aussi joué sur les enceintes (retour local). Si c'est le cas,
  décocher « M'entendre aussi », ou couper l'annulation d'écho dans l'application.
- **Push-to-talk.** Avec le PTT du jeu, les sons ne passent que touche enfoncée.
- **Pilotes non compatibles.** Un micro sans section `FxProperties` (certains périphériques
  virtuels) n'accepte pas d'effet : il apparaît comme non compatible.
