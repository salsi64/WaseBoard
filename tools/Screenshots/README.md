# Captures d'écran de l'interface

Outil de développement (jamais livré) : affiche la **vraie** interface de WaseBoard avec des données d'exemple et l'enregistre en PNG — pour le site
(`server/deploy/landing-page/img/`), le README (`docs/img/`) et la FAQ. Il pilote l'application par réflexion (champs et méthodes privés de
`MainWindow`…) : si l'interface change, il peut falloir l'ajuster.

```powershell
# depuis la racine du dépôt (toujours -c Dev : l'application cible est alors WaseBoard-Dev, avec son propre dossier de réglages)
dotnet run --project tools/Screenshots -c Dev -- sortie.png Flat win settings          # une fenêtre
dotnet run --project tools/Screenshots -c Dev -- sortie.png NightBlue site 880 528     # capture « vitrine » de la fenêtre principale (880 × 528 → PNG 1760 × 1056)
dotnet run --project tools/Screenshots -c Dev -- sortie.png Flat main NightBlue 1100   # fenêtre principale
```

| Variable | Effet |
|---|---|
| `WASEBOARD_LANG=en` (ou `fr`) | langue de l'interface (les noms de sons d'exemple de la capture « vitrine » suivent) |
| `SHOT_SCALE=2` | facteur d'échelle du PNG (défaut 1,5 pour une fenêtre, 2 pour la capture vitrine) |

Fenêtres (`win`) : `settings`, `discord`, `micpage`, `clippage`, `diag`, `cliptrim`, `onboarding`, `confirm`, `prompt`, `alert`, `emoji`, `hotkey`, `edit`,
`editlocal`, `siteedit`, `glyphs`. Palettes : `NightBlue`, `Violet`, `Mint`, `Rose`, `Amber`.

Une capture échoue parfois (« NullReferenceException » au démarrage de WPF) : relancer la commande. Ne pas lancer l'outil pendant que le client Dev tourne
(même verrou « une seule instance »).

Les captures du site portent un **nom neuf** quand elles changent (`hero-flat-3.4.png`) : les navigateurs gardent l'ancienne image en cache sinon.
