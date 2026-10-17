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

// ---------- Préférences : favoris, couleurs, catégories personnelles et leur ordre ----------
// Elles sont partagées avec l'application Windows par le serveur (POST /me/preferences/sync, fusionnées côté serveur : voir
// server/preferences.py). Ici, le modèle et ses modifications, sans réseau ni navigateur.
//   { favorites: [ids de sons], colors: { idDeSon: '#RRGGBB' }, categories: [{ name, ids }], order: [clés] }
// « order » est l'ordre d'affichage de l'application : noms de catégories et identifiants de serveurs mélangés. Les catégories sont une
// liste (et non un objet) pour que n'importe quel nom, même « __proto__ », reste un simple nom.

export const CATEGORY_NAME_MAX = 100;
const ID_MAX = 200;
const validId = (value) => typeof value === 'string' && value.length > 0 && value.length <= ID_MAX;
const unique = (items) => [...new Set(items)];

export function emptyPrefs() {
  return { favorites: [], colors: {}, categories: [], order: [] };
}

/** Préférences lues du serveur (ou du stockage du navigateur), nettoyées : jamais d'exception, ce qui est invalide est ignoré. */
export function sanitizePrefs(value) {
  const result = emptyPrefs();
  if (!value || typeof value !== 'object') return result;
  if (Array.isArray(value.favorites)) result.favorites = unique(value.favorites.filter(validId));
  const colors = value.colors;
  if (colors && typeof colors === 'object' && !Array.isArray(colors)) {
    for (const [id, color] of Object.entries(colors)) if (validId(id) && isHexColor(color)) result.colors[id] = color.toUpperCase();
  }
  const categories = value.categories;
  if (categories && typeof categories === 'object' && !Array.isArray(categories)) {
    for (const [rawName, ids] of Object.entries(categories)) {
      const name = rawName.trim();
      if (name.length > 0 && [...name].length <= CATEGORY_NAME_MAX && Array.isArray(ids)) {
        const known = result.categories.find((category) => category.name === name);
        const list = unique(ids.filter(validId));
        if (known) known.ids = unique([...known.ids, ...list]); else result.categories.push({ name, ids: list });
      }
    }
  }
  if (Array.isArray(value.order)) result.order = unique(value.order.filter(validId));
  return result;
}

/** Forme envoyée au serveur (les catégories redeviennent un objet). */
export function prefsToWire(prefs) {
  return {
    favorites: prefs.favorites,
    colors: prefs.colors,
    categories: Object.fromEntries(prefs.categories.map((category) => [category.name, category.ids])),   // fromEntries : « __proto__ » reste une clé
    order: prefs.order,
  };
}

/** Mêmes préférences ? Favoris et contenu d'une catégorie sont des ensembles ; l'ordre d'affichage est une suite. */
export function prefsEqual(a, b) {
  const sameSet = (x, y) => x.length === y.length && x.every((item) => y.includes(item));
  if (!sameSet(a.favorites, b.favorites) || a.order.length !== b.order.length || a.order.some((key, index) => key !== b.order[index])) return false;
  const colorsA = Object.entries(a.colors);
  if (colorsA.length !== Object.keys(b.colors).length || colorsA.some(([id, color]) => b.colors[id] !== color)) return false;
  if (a.categories.length !== b.categories.length) return false;
  return a.categories.every((category) => {
    const other = b.categories.find((item) => item.name === category.name);
    return other !== undefined && sameSet(category.ids, other.ids);
  });
}

/** Nom de catégorie valide ? { name } (rogné) ou { error: 'empty' | 'long' | 'exists' } ; `current` : le nom actuel quand on renomme. */
export function checkCategoryName(prefs, raw, current = null) {
  const name = String(raw ?? '').trim();
  if (!name) return { error: 'empty' };
  if ([...name].length > CATEGORY_NAME_MAX) return { error: 'long' };
  if (name !== current && prefs.categories.some((category) => category.name === name)) return { error: 'exists' };
  return { name };
}

export function toggleFavorite(prefs, id) {
  return { ...prefs, favorites: prefs.favorites.includes(id) ? prefs.favorites.filter((item) => item !== id) : [...prefs.favorites, id] };
}

/** Couleur choisie pour un son ('#RRGGBB'), ou null pour revenir à la couleur automatique. */
export function setColor(prefs, id, color) {
  const colors = { ...prefs.colors };
  if (isHexColor(color)) colors[id] = color.toUpperCase(); else delete colors[id];
  return { ...prefs, colors };
}

export function addCategory(prefs, name) {
  if (prefs.categories.some((category) => category.name === name)) return prefs;
  return { ...prefs, categories: [...prefs.categories, { name, ids: [] }], order: prefs.order.includes(name) ? prefs.order : [...prefs.order, name] };
}

export function removeCategory(prefs, name) {
  return { ...prefs, categories: prefs.categories.filter((category) => category.name !== name), order: prefs.order.filter((key) => key !== name) };
}

/** Renomme en gardant le contenu et la place (dans la liste et dans l'ordre d'affichage). */
export function renameCategory(prefs, oldName, newName) {
  if (oldName === newName || prefs.categories.some((category) => category.name === newName)) return prefs;
  return {
    ...prefs,
    categories: prefs.categories.map((category) => (category.name === oldName ? { ...category, name: newName } : category)),
    order: prefs.order.map((key) => (key === oldName ? newName : key)),
  };
}

/** Met un son dans une catégorie (member = true) ou l'en retire. Rien à changer : renvoie les mêmes préférences (le même objet). */
export function setMembership(prefs, name, soundId, member) {
  const category = prefs.categories.find((item) => item.name === name);
  if (!category || category.ids.includes(soundId) === member) return prefs;
  const ids = member ? [...category.ids, soundId] : category.ids.filter((id) => id !== soundId);
  return { ...prefs, categories: prefs.categories.map((item) => (item === category ? { ...item, ids } : item)) };
}

/** Déplace une catégorie d'un cran (delta = -1 : vers le haut, +1 : vers le bas) parmi les catégories ET les serveurs, comme dans l'application. */
export function moveCategory(prefs, guilds, name, delta) {
  const keys = orderedEntries(guilds, prefs).map((entry) => (entry.type === 'guild' ? entry.guild.id : entry.name));
  const from = keys.indexOf(name);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= keys.length) return prefs;
  [keys[from], keys[to]] = [keys[to], keys[from]];
  return { ...prefs, order: keys };
}

/** Place de la catégorie dans l'ordre d'affichage : { first, last } (pour désactiver « Monter » / « Descendre »). */
export function categoryPosition(prefs, guilds, name) {
  const keys = orderedEntries(guilds, prefs).map((entry) => (entry.type === 'guild' ? entry.guild.id : entry.name));
  const index = keys.indexOf(name);
  return { first: index <= 0, last: index < 0 || index === keys.length - 1 };
}

/** Clé d'onglet / de section d'une catégorie personnelle (préfixée : jamais confondue avec un identifiant de serveur). */
export const personalKey = (name) => `cat:${name}`;

/**
 * Serveurs et catégories personnelles dans l'ordre de l'application Windows ; ce que l'ordre ne cite pas vient ensuite
 * (serveurs d'abord, puis catégories), comme avant que les catégories personnelles existent ici.
 * @returns [{ type: 'guild', guild } | { type: 'personal', name }]
 */
export function orderedEntries(guilds, personal) {
  const categoryNames = ((personal && personal.categories) || []).map((category) => category.name);
  const names = new Set(categoryNames);
  const guildById = new Map(guilds.map((guild) => [guild.id, guild]));
  const entries = [];
  const seen = new Set();
  const add = (entry, id) => { if (!seen.has(id)) { seen.add(id); entries.push(entry); } };
  for (const key of (personal && personal.order) || []) {
    if (names.has(key)) add({ type: 'personal', name: key }, `p:${key}`);
    else if (guildById.has(key)) add({ type: 'guild', guild: guildById.get(key) }, `g:${key}`);
  }
  for (const guild of guilds) add({ type: 'guild', guild }, `g:${guild.id}`);
  for (const name of categoryNames) add({ type: 'personal', name }, `p:${name}`);
  return entries;
}

/** Sons du catalogue qui sont dans la catégorie personnelle de ce nom (un son supprimé ou devenu invisible n'y figure plus). */
function personalMembers(sounds, personal, name) {
  const category = ((personal && personal.categories) || []).find((item) => item.name === name);
  const ids = new Set(category ? category.ids : []);
  return sounds.filter((sound) => ids.has(sound.id));
}

/**
 * Onglets de la page : un par serveur Discord et un par catégorie personnelle (même vide : on peut la remplir depuis la page), dans
 * l'ordre de l'application Windows.
 * @returns [{ key, type: 'guild', guild } | { key, type: 'personal', name }]
 */
export function categoryTabs(sounds, guilds, personal) {
  const tabs = [];
  for (const entry of orderedEntries(guilds, personal)) {
    if (entry.type === 'guild') tabs.push({ key: entry.guild.id, ...entry });
    else tabs.push({ key: personalKey(entry.name), ...entry });
  }
  return tabs;
}

/**
 * Sections de la page : une par serveur Discord (ses sons + ceux partagés dans sa catégorie) et une par catégorie personnelle,
 * dans l'ordre de l'application Windows, puis « autres » pour un son qui n'appartient à aucun de ces serveurs. Un son partagé
 * apparaît dans chaque serveur où il l'est, et dans chaque catégorie personnelle qui le contient (comme dans l'application).
 * @param sounds   catalogue visible (GET /sounds)
 * @param guilds   serveurs de la personne (GET /my-guilds) : [{id, name, icon_url}]
 * @param shared   { idDuServeur: [idsDeSons] } (GET /shared-categories)
 * @param personal préférences nettoyées (sanitizePrefs), facultatif
 */
export function buildSections(sounds, guilds, shared, personal) {
  const placed = new Set();
  const sections = [];
  for (const entry of orderedEntries(guilds, personal)) {
    if (entry.type === 'personal') {
      sections.push({ key: personalKey(entry.name), kind: 'personal', guild: null, name: entry.name, sounds: sortByName(personalMembers(sounds, personal, entry.name)) });
      continue;
    }
    const guild = entry.guild;
    const sharedIds = new Set((shared && shared[guild.id]) || []);
    const members = sounds.filter((sound) => sound.guild_id === guild.id || sharedIds.has(sound.id));
    if (members.length === 0) continue;
    members.forEach((sound) => placed.add(sound.id));
    sections.push({ key: guild.id, kind: 'guild', guild, sounds: sortByName(members) });
  }
  const others = sounds.filter((sound) => !placed.has(sound.id));
  if (others.length > 0) sections.push({ key: 'other', kind: 'other', guild: null, sounds: sortByName(others) });
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

// ---------- Activité Discord ----------

/** La page est-elle chargée comme Activité dans Discord ? Discord ajoute « frame_id » (et « instance_id »…) à l'adresse de l'iframe. */
export function isActivityLocation(search) {
  return new URLSearchParams(search).has('frame_id');
}

/** Dans une Activité, seul le domaine de l'application est joignable : le CDN de Discord passe par une correspondance d'adresse
 *  « /discord-cdn » → cdn.discordapp.com (à déclarer dans le portail, voir README). Renvoie l'adresse de remplacement, ou null. */
export function cdnFallbackUrl(url) {
  const match = /^https:\/\/cdn\.discordapp\.com\/(.+)$/.exec(String(url || ''));
  return match ? `/discord-cdn/${match[1]}` : null;
}
