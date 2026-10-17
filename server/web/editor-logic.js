// Logique pure de l'éditeur de sons (ajout, modification, découpe) : aucune dépendance au navigateur, testée avec Node
// (server/tests/web/editor-logic.test.mjs). Les règles reprennent celles de l'application Windows (fenêtre « Ajouter / Éditer
// le son ») : emoji obligatoire à l'ajout, portion d'au moins 100 ms, son entier = aucune découpe enregistrée, doublons signalés.
import { trimRange } from './logic.js';

export const ALLOWED_EXTENSIONS = ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.wma'];
export const MAX_UPLOAD_BYTES = 64 * 1024 * 1024;      // limite du serveur (client_max_size)
export const MAX_DECODE_BYTES = 40 * 1024 * 1024;      // au-delà, le navigateur ne décode pas (mémoire) : pas de forme d'onde
export const MIN_TRIM_SECONDS = 0.1;
export const FULL_RANGE_TOLERANCE = 0.02;              // à 20 ms du début et de la fin = son entier
export const PEAK_BUCKETS = 900;
export const MAX_NAME_LENGTH = 100;

// ---------- Fichiers ----------

export function extensionOf(filename) {
  const match = /\.[^./\\]+$/.exec(String(filename || ''));
  return match ? match[0].toLowerCase() : '';
}

export function isAllowedFilename(filename) {
  return ALLOWED_EXTENSIONS.includes(extensionOf(filename));
}

/** Nom proposé pour un fichier : son nom sans dossier ni extension. */
export function defaultNameFromFile(filename) {
  const base = String(filename || '').replace(/^.*[\\/]/, '');
  const dot = base.lastIndexOf('.');
  const stem = dot > 0 ? base.slice(0, dot) : base;
  return stem.trim().slice(0, MAX_NAME_LENGTH) || 'Son';
}

export function audioFiles(fileList) {
  return Array.from(fileList || []).filter((file) => isAllowedFilename(file.name));
}

/** Empreinte SHA-256 (hexadécimale) d'un contenu, comme celle que le serveur calcule : détecte « ce fichier est déjà là ». */
export async function sha256Hex(buffer, subtle) {
  const digest = await subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

// ---------- Forme d'onde ----------

/** Crête (0 à 1, normalisée sur la plus forte) de chacune des `buckets` tranches de temps égales du son. */
export function computePeaks(channels, buckets = PEAK_BUCKETS) {
  const peaks = new Float32Array(buckets);
  const length = channels.length ? channels[0].length : 0;
  if (length === 0) return peaks;
  const perBucket = length / buckets;
  let top = 0;
  for (let bucket = 0; bucket < buckets; bucket++) {
    const from = Math.floor(bucket * perBucket);
    const to = Math.max(from + 1, Math.floor((bucket + 1) * perBucket));
    let max = 0;
    for (const data of channels) {
      for (let i = from; i < to && i < data.length; i++) {
        const value = Math.abs(data[i]);
        if (value > max) max = value;
      }
    }
    peaks[bucket] = max;
    if (max > top) top = max;
  }
  if (top > 0) for (let bucket = 0; bucket < buckets; bucket++) peaks[bucket] /= top;
  return peaks;
}

// ---------- Portion gardée (secondes) ----------

/** Ramène une portion {start, end} dans [0, durée], d'au moins la durée minimale. */
export function clampSelection(selection, duration, minLength = MIN_TRIM_SECONDS) {
  const total = Math.max(0, Number.isFinite(duration) ? duration : 0);
  const min = Math.min(minLength, total);
  let start = Math.min(Math.max(0, Number.isFinite(selection.start) ? selection.start : 0), total);
  let end = Math.min(Math.max(0, Number.isFinite(selection.end) ? selection.end : total), total);
  if (end - start < min) {
    if (start + min <= total) end = start + min;
    else { end = total; start = Math.max(0, total - min); }
  }
  return { start, end };
}

/** Déplace une poignée (« start » ou « end ») au temps demandé sans jamais croiser l'autre ni sortir du son. */
export function moveHandle(selection, which, time, duration, minLength = MIN_TRIM_SECONDS) {
  const min = Math.min(minLength, duration);
  if (which === 'start') return { start: Math.max(0, Math.min(time, selection.end - min)), end: selection.end };
  return { start: selection.start, end: Math.min(duration, Math.max(time, selection.start + min)) };
}

export function isFullRange(selection, duration) {
  return selection.start <= FULL_RANGE_TOLERANCE && selection.end >= duration - FULL_RANGE_TOLERANCE;
}

/** Découpe à enregistrer : null pour le son entier (le serveur garde de toute façon le fichier complet), sinon en millisecondes. */
export function selectionToTrim(selection, duration) {
  if (isFullRange(selection, duration)) return null;
  return { trim_start_ms: Math.round(selection.start * 1000), trim_end_ms: Math.round(selection.end * 1000) };
}

/** Portion affichée à l'ouverture : celle déjà enregistrée pour ce son (bornée à sa durée), sinon le son entier. */
export function selectionFromSound(sound, duration) {
  const range = trimRange(sound);
  return range ? clampSelection(range, duration) : { start: 0, end: duration };
}

export function formatClock(seconds) {
  const total = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const minutes = Math.floor(total / 60);
  return `${minutes}:${(total - minutes * 60).toFixed(1).padStart(4, '0')}`;
}

// ---------- Emoji ----------

export const EMOJI_CHOICES = [
  '😀', '😂', '🤣', '😎', '🤯', '😱', '😭', '😡', '🥳', '😴', '🤔', '🙄', '🤡', '🗿', '💀', '👻',
  '👽', '🤖', '💩', '🥶', '🤮', '😈', '🫡', '👀', '👏', '🙌', '👍', '👎', '💪', '🖕', '🤌', '🙏',
  '🔥', '💥', '✨', '⚡', '🌈', '💯', '❤️', '💔', '🎉', '🎊', '🏆', '🥇', '🚨', '🔔', '📢', '🚀',
  '💣', '🔫', '🎵', '🎶', '🎺', '🎸', '🥁', '🎻', '🎤', '🎧', '🐱', '🐶', '🐸', '🐵', '🦆', '🐔',
  '🐘', '🦁', '🐍', '🧙', '🧟', '🍕', '🍔', '🍺', '🚗', '🏎️', '✈️', '⏰',
];

/** Premier caractère visible saisi (un emoji peut tenir en plusieurs points de code : famille, drapeau, teint…). */
export function firstGrapheme(text) {
  const value = String(text || '').trim();
  if (!value) return '';
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    const first = new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(value)[Symbol.iterator]().next();
    return first.done ? '' : first.value.segment;
  }
  return Array.from(value)[0];
}

export function isEmoji(text) {
  const value = String(text || '').trim();
  return value.length > 0 && value.length <= 32 && /\p{Extended_Pictographic}|\p{Regional_Indicator}|[0-9#*]️?⃣/u.test(value);
}

// ---------- Formulaire ----------

/**
 * Premier problème du formulaire, sous la forme { key, args } (clé de traduction), ou null s'il est valide.
 * mode « add » : fichier, serveur et emoji obligatoires ; mode « edit » : l'emoji n'est vérifié que s'il est renseigné
 * (d'anciens sons n'en ont pas) et un nouveau fichier éventuel doit être valide.
 */
export function validateForm({ mode, file, emoji, guildId, selection, duration, trimAvailable, replacementFile }) {
  const fileProblem = (candidate) => {
    if (!isAllowedFilename(candidate.name)) return { key: 'editor.error.format', args: [extensionOf(candidate.name) || '?', ALLOWED_EXTENSIONS.join(', ')] };
    if (candidate.size > MAX_UPLOAD_BYTES) return { key: 'editor.error.size', args: [] };
    return null;
  };
  if (mode === 'add') {
    if (!file) return { key: 'editor.error.file', args: [] };
    const problem = fileProblem(file);
    if (problem) return problem;
    if (!guildId) return { key: 'editor.error.guild', args: [] };
    if (!String(emoji || '').trim()) return { key: 'editor.error.emoji', args: [] };
  } else if (replacementFile) {
    const problem = fileProblem(replacementFile);
    if (problem) return problem;
  }
  if (String(emoji || '').trim() && !isEmoji(emoji)) return { key: 'editor.error.emojiInvalid', args: [] };
  if (trimAvailable && selection && selection.end - selection.start < MIN_TRIM_SECONDS - 1e-9) return { key: 'editor.error.trim', args: [] };
  return null;
}

/** Champs texte de l'envoi (POST /sounds) ; le fichier lui-même est ajouté par l'appelant. */
export function uploadFields({ name, emoji, guildId, trim }) {
  const fields = [['name', String(name).trim() || 'Son'], ['guild_id', String(guildId)], ['emoji', String(emoji || '').trim()]];
  if (trim) fields.push(['trim_start_ms', String(trim.trim_start_ms)], ['trim_end_ms', String(trim.trim_end_ms)]);
  return fields;
}

function sameTrim(a, b) {
  if (!a || !b) return !a && !b;
  return Math.abs(a.trim_start_ms - b.trim_start_ms) <= 10 && Math.abs(a.trim_end_ms - b.trim_end_ms) <= 10;
}

/** Corps de PATCH /sounds/{id} : seulement ce qui a changé ; null s'il n'y a rien à envoyer. Découpe retirée = deux valeurs nulles. */
export function patchBody(original, edited) {
  const body = {};
  if (edited.name !== original.name) body.name = edited.name;
  if (String(edited.emoji || '') !== String(original.emoji || '')) body.emoji = edited.emoji || '';
  if (!sameTrim(original.trim, edited.trim)) {
    body.trim_start_ms = edited.trim ? edited.trim.trim_start_ms : null;
    body.trim_end_ms = edited.trim ? edited.trim.trim_end_ms : null;
  }
  return Object.keys(body).length > 0 ? body : null;
}

// ---------- Doublons, serveurs, catégories partagées ----------

/** Un son identique (même contenu) ou du même nom existe-t-il déjà dans ce serveur (ses sons + ceux qui y sont partagés) ? */
export function findDuplicate(sounds, shared, guildId, { hash, name }) {
  const sharedIds = new Set((shared && shared[guildId]) || []);
  const scope = sounds.filter((sound) => sound.guild_id === guildId || sharedIds.has(sound.id));
  const sameContent = hash ? scope.find((sound) => sound.hash && sound.hash === hash) : null;
  if (sameContent) return { kind: 'content', sound: sameContent };
  const wanted = String(name || '').trim().toLocaleLowerCase();
  const sameName = wanted ? scope.find((sound) => String(sound.name).trim().toLocaleLowerCase() === wanted) : null;
  return sameName ? { kind: 'name', sound: sameName } : null;
}

export function uploadableGuilds(guilds) {
  return guilds.filter((guild) => guild.can_upload);
}

/** Serveur proposé par défaut pour un ajout : l'onglet ouvert, sinon celui du salon vocal, sinon le premier. */
export function defaultUploadGuild(guilds, { tab, voiceGuildId }) {
  const options = uploadableGuilds(guilds);
  return (options.find((guild) => guild.id === tab) || options.find((guild) => guild.id === voiceGuildId) || options[0] || null);
}

/**
 * Serveurs où l'on peut choisir d'afficher ce son : son auteur le peut partout, un admin dans son serveur (même règle que le
 * serveur, qui fait foi). `locked` : le serveur d'origine du son, dont on ne le retire pas.
 */
export function shareChoices(sound, guilds, shared) {
  return guilds
    .filter((guild) => sound.is_mine || guild.is_admin)
    .map((guild) => ({
      guildId: guild.id, guildName: guild.name, iconUrl: guild.icon_url || null,
      locked: guild.id === sound.guild_id,
      member: guild.id === sound.guild_id || ((shared && shared[guild.id]) || []).includes(sound.id),
    }));
}

/** Requêtes à faire pour passer des choix d'origine aux choix voulus (une par serveur modifié, jamais le serveur d'origine). */
export function diffShares(initial, desired) {
  const before = new Map(initial.map((choice) => [choice.guildId, choice.member]));
  return desired
    .filter((choice) => !choice.locked && before.has(choice.guildId) && before.get(choice.guildId) !== choice.member)
    .map((choice) => ({ guild_id: choice.guildId, action: choice.member ? 'add' : 'remove' }));
}
