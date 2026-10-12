# Textes de l'interface (fr / en)

Chaque langue est un fichier `fr.json`, `en.json`… : `"clé": "texte"`. Le **français est la référence** : toute clé doit
exister dans toutes les langues, avec les mêmes paramètres `{0}`, `{1}` (vérifié par `tools/check-localization.ps1`, lancé
par la CI).

- En C# : `Loc.T("update.available", version)`.
- En XAML : `xmlns:loc="clr-namespace:WaseBoard.Services.Localization"` puis `Content="{loc:Loc settings.title}"`.
- Règle : **aucun nouveau texte visible en dur**. Les textes existants seront migrés fenêtre par fenêtre.
- Clés : `zone.sujet` en minuscules (`settings.diagnostic.copy`, `toast.sound_added`).
- Un texte manquant retombe sur le français, puis sur la clé : l'application ne plante jamais pour une traduction oubliée.
- Langue : `Language` dans `settings.json` (`auto` suit Windows : français, sinon anglais) ; la variable d'environnement
  `WASEBOARD_LANG=en` force une langue pour les essais. Changer de langue demande de relancer l'application.
- Ajouter une langue : un nouveau fichier ici + son code dans `Loc.Supported`.
