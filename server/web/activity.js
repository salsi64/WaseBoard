// Activité Discord : la page, affichée dans Discord (iframe) au lieu d'un onglet. Ce module ne sert que dans ce cas — il charge le kit
// officiel de Discord (vendor/discord-embedded-app-sdk.js, ~150 Ko) à la demande, jamais dans un navigateur ordinaire.
//
// Déroulé (documentation « Building an Activity ») : le kit se présente à Discord (ready), la page demande un code d'autorisation à
// Discord SANS quitter l'iframe (authorize, sans fenêtre si la personne a déjà autorisé l'application), le serveur l'échange contre
// une session (POST /oauth/exchange, sans adresse de retour), puis le kit reçoit le jeton Discord de la personne (authenticate).
// Discord fournit aussi le serveur et le salon où l'Activité a été lancée : c'est eux qu'on utilise pour jouer.

/** Se connecte à Discord et renvoie { sdk, guildId, channelId } (guildId nul si lancée hors d'un serveur, en message privé par exemple). */
export async function connectActivity(config) {
  const { DiscordSDK } = await import('./vendor/discord-embedded-app-sdk.js');
  const sdk = new DiscordSDK(config.client_id);
  await sdk.ready();
  return { sdk, guildId: sdk.guildId || null, channelId: sdk.channelId || null };
}

/** Demande à Discord un code d'autorisation (portée « identify » seulement : le nom et l'avatar de la personne). */
export async function authorizeActivity(connection, config) {
  const { code } = await connection.sdk.commands.authorize({
    client_id: config.client_id,
    response_type: 'code',
    state: '',
    prompt: 'none',
    scope: ['identify'],
  });
  return code;
}

/** Donne au kit le jeton Discord de la personne. Facultatif pour jouer des sons : un échec ne bloque donc rien. */
export async function authenticateActivity(connection, accessToken) {
  try {
    await connection.sdk.commands.authenticate({ access_token: accessToken });
  } catch {
    /* le kit n'a besoin de ce jeton que pour des fonctions que la page n'utilise pas */
  }
}
