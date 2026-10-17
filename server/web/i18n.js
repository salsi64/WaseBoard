// Textes de la page web, en français et en anglais. Aucun texte visible n'est écrit en dur ailleurs (même règle que le client
// Windows) ; les messages d'erreur renvoyés par le serveur sont affichés tels quels.

export const LANGUAGES = ['fr', 'en'];

export const MESSAGES = {
  fr: {
    'app.name': 'WaseBoard',
    'login.title': 'Vos sons, dans votre vocal Discord',
    'login.intro': "Connectez-vous avec Discord pour retrouver les sons de vos serveurs et les jouer dans votre salon vocal, depuis n'importe quel appareil.",
    'login.button': 'Se connecter avec Discord',
    'login.signing': 'Connexion en cours…',
    'login.expired': 'Votre session a expiré : reconnectez-vous.',
    'login.loggedOut': 'Vous êtes déconnecté.',
    'login.error.state': 'La connexion a été interrompue (vérification de sécurité échouée). Réessayez.',
    'login.error.denied': "L'autorisation a été refusée sur Discord.",
    'login.error.exchange': 'Connexion impossible : {0}',
    'login.error.origin': "Ce serveur n'accepte pas la connexion depuis cette adresse. Utilisez l'adresse officielle du site.",
    'login.error.config': "Ce serveur n'a pas encore configuré la connexion Discord.",
    'login.error.offline': 'Serveur injoignable. Vérifiez votre connexion et réessayez.',
    'search.placeholder': 'Rechercher un son',
    'search.label': 'Rechercher un son',
    'tabs.label': 'Catégories',
    'tabs.favorites': 'Favoris',
    'tabs.all': 'Tous',
    'section.other': 'Autres sons',
    'empty.favorites': 'Aucun favori pour l’instant : touchez ⋯ sur un son, puis « Ajouter aux favoris ».',
    'empty.search': 'Aucun son ne correspond à « {0} ».',
    'empty.none': 'Aucun son disponible sur vos serveurs.',
    'loading': 'Chargement des sons…',
    'load.error': 'Impossible de charger les sons.',
    'retry': 'Réessayer',
    'sound.play': 'Jouer {0}',
    'sound.more': 'Options de {0}',
    'sound.playing': 'en cours de lecture',
    'voice.in': 'Dans {0}',
    'voice.inGuild': '{0} · {1}',
    'voice.none': 'Le bot n’est pas dans votre salon vocal',
    'voice.join': 'Rejoindre mon vocal',
    'voice.joined': 'Le bot a rejoint « {0} ».',
    'stop': 'Stop',
    'stop.done': 'Sons coupés.',
    'menu.open': 'Menu du compte',
    'menu.logout': 'Se déconnecter',
    'menu.language': 'Language : English',
    'menu.install': "Installer l'application",
    'menu.about': 'WaseBoard web · version {0}',
    'sheet.favorite.add': 'Ajouter aux favoris',
    'sheet.favorite.remove': 'Retirer des favoris',
    'sheet.color': 'Couleur',
    'sheet.color.auto': 'Automatique',
    'sheet.preview': 'Écouter sur cet appareil',
    'sheet.close': 'Fermer',
    'preview.error': 'Impossible de lire ce son ici ({0}).',
    'offline': 'Hors connexion : les sons ne peuvent pas être joués.',
    'online': 'Connexion rétablie.',
    'error.generic': 'Une erreur est survenue ({0}).',
    'noscript': 'WaseBoard a besoin de JavaScript pour fonctionner.',
  },
  en: {
    'app.name': 'WaseBoard',
    'login.title': 'Your sounds, in your Discord voice chat',
    'login.intro': 'Sign in with Discord to find your servers’ sounds and play them in your voice channel, from any device.',
    'login.button': 'Sign in with Discord',
    'login.signing': 'Signing in…',
    'login.expired': 'Your session has expired: please sign in again.',
    'login.loggedOut': 'You are signed out.',
    'login.error.state': 'Sign-in was interrupted (security check failed). Please try again.',
    'login.error.denied': 'Authorization was denied on Discord.',
    'login.error.exchange': 'Sign-in failed: {0}',
    'login.error.origin': 'This server does not accept sign-in from this address. Use the official address of the site.',
    'login.error.config': 'This server has not set up Discord sign-in yet.',
    'login.error.offline': 'Server unreachable. Check your connection and try again.',
    'search.placeholder': 'Search a sound',
    'search.label': 'Search a sound',
    'tabs.label': 'Categories',
    'tabs.favorites': 'Favorites',
    'tabs.all': 'All',
    'section.other': 'Other sounds',
    'empty.favorites': 'No favorites yet: tap ⋯ on a sound, then “Add to favorites”.',
    'empty.search': 'No sound matches “{0}”.',
    'empty.none': 'No sounds available on your servers.',
    'loading': 'Loading sounds…',
    'load.error': 'Could not load the sounds.',
    'retry': 'Retry',
    'sound.play': 'Play {0}',
    'sound.more': 'Options for {0}',
    'sound.playing': 'now playing',
    'voice.in': 'In {0}',
    'voice.inGuild': '{0} · {1}',
    'voice.none': 'The bot is not in your voice channel',
    'voice.join': 'Join my voice channel',
    'voice.joined': 'The bot joined “{0}”.',
    'stop': 'Stop',
    'stop.done': 'Sounds stopped.',
    'menu.open': 'Account menu',
    'menu.logout': 'Sign out',
    'menu.language': 'Langue : Français',
    'menu.install': 'Install the app',
    'menu.about': 'WaseBoard web · version {0}',
    'sheet.favorite.add': 'Add to favorites',
    'sheet.favorite.remove': 'Remove from favorites',
    'sheet.color': 'Color',
    'sheet.color.auto': 'Automatic',
    'sheet.preview': 'Listen on this device',
    'sheet.close': 'Close',
    'preview.error': 'Could not play this sound here ({0}).',
    'offline': 'Offline: sounds cannot be played.',
    'online': 'Back online.',
    'error.generic': 'Something went wrong ({0}).',
    'noscript': 'WaseBoard needs JavaScript to work.',
  },
};

/** Première langue prise en charge parmi celles du navigateur (« fr-CA » → « fr »), français à défaut. */
export function pickLanguage(preferred) {
  for (const tag of preferred || []) {
    const base = String(tag).toLowerCase().split('-')[0];
    if (LANGUAGES.includes(base)) return base;
  }
  return 'fr';
}

/** Traducteur : t('clé', arg0, arg1…) remplace {0}, {1}… ; clé inconnue → français, puis la clé elle-même. */
export function makeTranslator(language) {
  const table = MESSAGES[language] || MESSAGES.fr;
  return (key, ...args) => {
    const template = table[key] ?? MESSAGES.fr[key] ?? key;
    return template.replace(/\{(\d+)\}/g, (match, index) => (args[Number(index)] ?? match));
  };
}
