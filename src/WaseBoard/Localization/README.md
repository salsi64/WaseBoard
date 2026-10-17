# Textes de l'interface (fr / en)

Chaque langue est un fichier `fr.json`, `en.json`… : `"clé": "texte"`. Le **français est la référence** : toute clé doit
exister dans toutes les langues, avec les mêmes paramètres `{0}`, `{1}` (vérifié par `tools/check-localization.ps1`, lancé
par la CI).

- En C# : `Loc.T("update.available", version)`.
- En XAML : `xmlns:loc="clr-namespace:WaseBoard.Services.Localization"` puis `Content="{loc:Loc settings.title}"`.
- Règle : **aucun texte visible en dur** (migration terminée en 3.4). `tools/check-localization.ps1` (lancé par la CI) refuse une chaîne C#
  ou un attribut XAML contenant une lettre accentuée française ; une ligne marquée `// loc-ok` y échappe (nom d'une langue dans sa langue, par exemple).
  Le test `Every_key_used_in_the_sources_exists` vérifie que chaque `Loc.T("clé")` / `{loc:Loc clé}` existe.
- Les messages d'erreur du serveur sont rédigés dans la langue de l'interface : le client envoie `Accept-Language: fr|en` (voir `server/messages.py`).
- Clés : `zone.sujet` en minuscules (`settings.diagnostic.copy`, `toast.sound_added`).
- Un texte manquant retombe sur le français, puis sur la clé : l'application ne plante jamais pour une traduction oubliée.
- Langue : `Language` dans `settings.json` (`auto` suit Windows : français, sinon anglais) ; la variable d'environnement
  `WASEBOARD_LANG=en` force une langue pour les essais. Changer de langue demande de relancer l'application.
- Ajouter une langue : un nouveau fichier ici + son code dans `Loc.Supported`.
