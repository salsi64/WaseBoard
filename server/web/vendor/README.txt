Fichiers tiers embarqués (non modifiés à la main).

discord-embedded-app-sdk.js
  Kit de Discord pour les Activités (« Embedded App SDK »), version 2.5.0, licence MIT (voir LICENSE-discord-embedded-app-sdk.txt).
  Chargé seulement quand la page tourne comme Activité dans Discord (jamais dans le navigateur) : voir activity.js.
  SHA-256 : 3132308bf7078cf0c88a6e041a885e1333b6151c8d6174851dd892ad5da09ad8 (vérifié par tests/web/bundle.test.mjs)

  Reconstruction (dans un dossier vide, scripts d'installation désactivés) :
    npm init -y
    npm install --ignore-scripts esbuild@0.25.10 @discord/embedded-app-sdk@2.5.0
    echo 'export { DiscordSDK, DiscordSDKMock, Events, Platform } from "@discord/embedded-app-sdk";' > entry.mjs
    npx esbuild entry.mjs --bundle --format=esm --minify --target=es2020 --legal-comments=none --outfile=discord-embedded-app-sdk.js

  Mise à jour : refaire la reconstruction avec la nouvelle version, puis mettre à jour le SHA-256 ci-dessus.
