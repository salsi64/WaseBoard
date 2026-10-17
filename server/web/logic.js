// Logique pure de la page web : aucune dépendance au navigateur (le DOM est dans app.js), donc testée telle quelle avec
// Node (server/tests/web/). Les couleurs automatiques reprennent EXACTEMENT l'algorithme de l'application Windows
// (IdToColorConverter) : un son a la même couleur partout.

export const PALETTE = ['#F97316', '#8B5CF6', '#14B8A6', '#EC4899', '#3B82F6', '#EAB308', '#10B981', '#EF4444', '#6366F1', '#06B6D4'];

/** Couleur automatique d'un son, stable pour un même identifiant (hash « × 31 + caractère » sur 32 bits, comme en C#). */
export function autoColor(id) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (Math.imul(hash, 31) + id.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

export function isHexColor(value) {
  return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value);
}

/** Couleur effective : celle choisie dans ce navigateur si elle est valide, sinon l'automatique. */
export function colorFor(sound, custom) {
  const chosen = custom && custom[sound.id];
  return isHexColor(chosen) ? chosen : autoColor(sound.id);
}

/** Texte comparable pour la recherche : sans accents ni majuscules. */
export function normalizeText(value) {
  return String(value ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLocaleLowerCase();
}

/** Sons dont le nom contient tous les mots de la recherche (accents et casse ignorés). Recherche vide : tout. */
export function filterSounds(sounds, query) {
  const words = normalizeText(query).trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return sounds;
  return sounds.filter((sound) => {
    const name = normalizeText(sound.name);
    return words.every((word) => name.includes(word));
  });
}

export function sortByName(sounds) {
  return [...sounds].sort((a, b) => String(a.name).localeCompare(String(b.name), undefined, { sensitivity: 'base', numeric: true }));
}

export function dedupeById(sounds) {
  const seen = new Set();
  return sounds.filter((sound) => !seen.has(sound.id) && seen.add(sound.id));
}

/**
 * Sections de la page : une par serveur Discord (ses sons + ceux partagés dans sa catégorie), dans l'ordre des serveurs,
 * puis « autres » pour un son qui n'appartient à aucun de ces serveurs. Un son partagé apparaît dans chaque serveur où il l'est.
 * @param sounds   catalogue visible (GET /sounds)
 * @param guilds   serveurs de la personne (GET /my-guilds) : [{id, name, icon_url}]
 * @param shared   { idDuServeur: [idsDeSons] } (GET /shared-categories)
 */
export function buildSections(sounds, guilds, shared) {
  const placed = new Set();
  const sections = [];
  for (const guild of guilds) {
    const sharedIds = new Set((shared && shared[guild.id]) || []);
    const members = sounds.filter((sound) => sound.guild_id === guild.id || sharedIds.has(sound.id));
    if (members.length === 0) continue;
    members.forEach((sound) => placed.add(sound.id));
    sections.push({ key: guild.id, guild, sounds: sortByName(members) });
  }
  const others = sounds.filter((sound) => !placed.has(sound.id));
  if (others.length > 0) sections.push({ key: 'other', guild: null, sounds: sortByName(others) });
  return sections;
}

/** Favoris : sons du catalogue dont l'identifiant est dans `favoriteIds` (un son supprimé disparaît tout seul). */
export function favoriteSounds(sounds, favoriteIds) {
  return sortByName(dedupeById(sounds).filter((sound) => favoriteIds.has(sound.id)));
}

export function toggleInSet(set, id) {
  const next = new Set(set);
  if (!next.delete(id)) next.add(id);
  return next;
}

// ---------- Stockage local (favoris, couleurs, session) : jamais d'exception si le navigateur le refuse ----------

export function loadJson(storage, key, fallback) {
  try {
    const raw = storage.getItem(key);
    return raw === null || raw === undefined ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function saveJson(storage, key, value) {
  try {
    storage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

/** Liste d'identifiants lue du stockage → ensemble (tout ce qui n'est pas une chaîne est ignoré). */
export function sanitizeIds(value) {
  return new Set(Array.isArray(value) ? value.filter((id) => typeof id === 'string' && id.length > 0 && id.length <= 200) : []);
}

/** Couleurs choisies lues du stockage : seules les entrées { identifiant: '#RRGGBB' } valides sont gardées. */
export function sanitizeColors(value) {
  const result = {};
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [id, color] of Object.entries(value)) if (isHexColor(color)) result[id] = color;
  }
  return result;
}

/** Session enregistrée : valide seulement avec un jeton texte ; sinon on repart de la connexion. */
export function sanitizeSession(value) {
  if (!value || typeof value !== 'object' || typeof value.session_token !== 'string' || value.session_token.length < 16) return null;
  return {
    session_token: value.session_token,
    user_id: String(value.user_id ?? ''),
    username: String(value.username ?? ''),
    avatar_url: typeof value.avatar_url === 'string' && value.avatar_url.startsWith('https://cdn.discordapp.com/') ? value.avatar_url : null,
  };
}

// ---------- Connexion Discord (OAuth2) ----------

/** Valeur aléatoire du paramètre « state » : protège la connexion d'une redirection forgée par un tiers. */
export function makeState(randomBytes) {
  return Array.from(randomBytes(16), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function buildAuthorizeUrl(config, state) {
  const url = new URL('https://discord.com/oauth2/authorize');
  url.searchParams.set('client_id', config.client_id);
  url.searchParams.set('redirect_uri', config.redirect_uri);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('scope', config.scope || 'identify');
  url.searchParams.set('state', state);
  return url.toString();
}

/** Lit l'adresse de retour de Discord : { code, state, error } (null pour ce qui manque). */
export function parseCallback(search) {
  const params = new URLSearchParams(search);
  return { code: params.get('code'), state: params.get('state'), error: params.get('error') };
}

// ---------- Réponses du serveur ----------

/** Message d'erreur lisible d'une réponse de l'API : le champ « error » du serveur s'il existe, sinon le code HTTP. */
export function errorText(status, body) {
  if (body && typeof body.error === 'string' && body.error.length > 0) return body.error;
  return status > 0 ? `HTTP ${status}` : 'réseau';
}

/** Portion jouée d'un son découpé, en secondes ({start, end}), ou null pour le son entier. */
export function trimRange(sound) {
  const start = Number.isInteger(sound.trim_start_ms) ? sound.trim_start_ms : null;
  const end = Number.isInteger(sound.trim_end_ms) ? sound.trim_end_ms : null;
  if (start === null || end === null || end <= start) return null;
  return { start: start / 1000, end: end / 1000 };
}

/** Son → utilisateurs qui le jouent en ce moment, d'après la réponse de GET /activity ({ activity: { idSon: [utilisateurs] } }). */
export function playingMap(activityResponse) {
  const map = new Map();
  const activity = activityResponse && activityResponse.activity;
  if (activity && typeof activity === 'object') {
    for (const [soundId, users] of Object.entries(activity)) {
      if (Array.isArray(users) && users.length > 0) map.set(soundId, users);
    }
  }
  return map;
}

/** Résumé de l'état vocal d'après GET /status. */
export function voiceSummary(status) {
  if (!status || !status.connected) return { connected: false, channel: null, guildName: null, guildId: null };
  return {
    connected: true,
    channel: status.channel || null,
    guildName: status.guild_name || null,
    guildId: status.guild_id || null,
  };
}
