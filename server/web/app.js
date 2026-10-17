// WaseBoard web : l'interface. Toute la logique testable est dans logic.js ; ici, le navigateur (DOM, réseau, stockage).
// Règles : jamais de HTML construit à partir de données (les noms de sons et pseudos sont du texte), aucun style en ligne (CSP),
// l'API est celle de l'application Windows, authentifiée par la session Discord en « Authorization: Bearer ».
import { LANGUAGES, makeTranslator, pickLanguage } from './i18n.js';
import {
  PALETTE, buildAuthorizeUrl, buildSections, colorFor, dedupeById, errorText, favoriteSounds, filterSounds, loadJson, makeState,
  parseCallback, playingMap, sanitizeColors, sanitizeIds, sanitizeSession, saveJson, sortByName, toggleInSet, trimRange, voiceSummary,
} from './logic.js';

const KEYS = { session: 'wb.session', favorites: 'wb.favorites', colors: 'wb.colors', lang: 'wb.lang', oauthState: 'wb.oauth_state' };
const LOGO = '/app/icons/icon-192.png';

// ---------- Stockage (le navigateur peut le refuser : on retombe sur la mémoire de la page) ----------
function openStorage(kind) {
  try {
    const store = window[kind];
    store.setItem('wb.probe', '1');
    store.removeItem('wb.probe');
    return store;
  } catch {
    const memory = new Map();
    return { getItem: (k) => (memory.has(k) ? memory.get(k) : null), setItem: (k, v) => memory.set(k, String(v)), removeItem: (k) => memory.delete(k) };
  }
}
const storage = openStorage('localStorage');
const tabStorage = openStorage('sessionStorage');

// ---------- État ----------
let lang = LANGUAGES.includes(storage.getItem(KEYS.lang)) ? storage.getItem(KEYS.lang) : pickLanguage(navigator.languages || [navigator.language]);
let t = makeTranslator(lang);
const state = {
  config: null,
  session: sanitizeSession(loadJson(storage, KEYS.session, null)),
  sounds: [], guilds: [], shared: {},
  voice: voiceSummary(null),
  playing: new Map(),
  tab: 'all', query: '',
  favorites: sanitizeIds(loadJson(storage, KEYS.favorites, [])),
  colors: sanitizeColors(loadJson(storage, KEYS.colors, {})),
  load: 'idle', // idle | loading | error | ready
  installPrompt: null,
};
const ui = {};            // éléments de la page principale, quand elle est affichée
let timers = {};
let busy = { status: false, activity: false };
let preview = null;       // { audio, url } : écoute locale en cours

const root = document.getElementById('root');
const toastLayer = document.getElementById('toasts');

// ---------- Petits outils DOM ----------
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key === 'vars') for (const [name, v] of Object.entries(value)) el.style.setProperty(name, v);
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) el.append(child.nodeType ? child : document.createTextNode(String(child)));
  return el;
}

function avatar(url, size) {
  return url ? h('img', { src: url, alt: '', width: size, height: size, referrerpolicy: 'no-referrer', loading: 'lazy' }) : null;
}

function toast(message, kind = 'info') {
  const el = h('div', { class: `toast ${kind}`, text: message });
  toastLayer.append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 5000 : 3000);
}

// ---------- Réseau ----------
async function api(path, { method = 'GET', body } = {}) {
  const headers = {};
  if (state.session) headers.Authorization = `Bearer ${state.session.session_token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, { method, headers, credentials: 'omit', cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    return { ok: false, status: 0, data: null, offline: true };
  }
  let data = null;
  try { data = await response.json(); } catch { /* corps vide ou pas du JSON */ }
  if (response.status === 401 && state.session) endSession('login.expired');
  return { ok: response.ok, status: response.status, data };
}

async function fetchConfig() {
  if (state.config) return state.config;
  const response = await fetch('/app/api/config', { cache: 'no-store', credentials: 'omit' });
  if (!response.ok) throw new Error(response.status === 503 ? 'config' : `HTTP ${response.status}`);
  state.config = await response.json();
  return state.config;
}

// ---------- Session ----------
function saveSession(session) {
  state.session = session;
  saveJson(storage, KEYS.session, session);
}

function endSession(messageKey) {
  state.session = null;
  storage.removeItem(KEYS.session);
  stopPolling();
  stopPreview();
  showLogin(messageKey ? { text: t(messageKey) } : null);
}

async function logout() {
  const token = state.session && state.session.session_token;
  if (token) {
    // Le serveur oublie la session (et les jetons Discord qu'elle contient). Appel direct : un 401 ici n'est pas une session expirée.
    try { await fetch('/oauth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, credentials: 'omit' }); } catch { /* hors connexion : on oublie quand même la session ici */ }
  }
  endSession('login.loggedOut');
}

// ---------- Connexion ----------
function showLogin(notice) {
  root.replaceChildren(
    h('main', { class: 'login' },
      h('div', { class: 'login-card' },
        h('img', { class: 'login-logo', src: LOGO, alt: '', width: 96, height: 96 }),
        h('h1', { text: t('login.title') }),
        h('p', { text: t('login.intro') }),
        notice ? h('p', { class: `notice ${notice.error ? 'error' : ''}`, text: notice.text }) : null,
        h('button', { class: 'btn primary', id: 'login', onclick: startLogin, text: t('login.button') }),
        h('p', {}, h('button', { class: 'btn', onclick: toggleLanguage, text: t('menu.language') }))),
    ));
  ui.main = null;
}

async function startLogin(event) {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = t('login.signing');
  let config;
  try {
    config = await fetchConfig();
  } catch (error) {
    showLogin({ error: true, text: error.message === 'config' ? t('login.error.config') : t('login.error.offline') });
    return;
  }
  const bytes = (n) => crypto.getRandomValues(new Uint8Array(n));
  const stateValue = makeState(bytes);
  tabStorage.setItem(KEYS.oauthState, stateValue);
  window.location.assign(buildAuthorizeUrl(config, stateValue));
}

/** Retour de Discord sur /app/callback : vérifie « state », échange le code contre une session, puis ouvre l'application. */
async function handleCallback() {
  const { code, state: returned, error } = parseCallback(window.location.search);
  const expected = tabStorage.getItem(KEYS.oauthState);
  tabStorage.removeItem(KEYS.oauthState);
  history.replaceState(null, '', '/app/');   // le code d'autorisation ne doit pas rester dans l'adresse ni l'historique

  if (error) return showLogin({ error: true, text: t('login.error.denied') });
  if (!code || !returned || !expected || returned !== expected) return showLogin({ error: true, text: t('login.error.state') });

  root.replaceChildren(h('main', { class: 'login' }, h('div', { class: 'login-card' }, h('p', { text: t('login.signing') }))));
  let config;
  try { config = await fetchConfig(); } catch { return showLogin({ error: true, text: t('login.error.offline') }); }

  const result = await api('/oauth/exchange', { method: 'POST', body: { code, redirect_uri: config.redirect_uri } });
  if (result.offline) return showLogin({ error: true, text: t('login.error.offline') });
  if (!result.ok) {
    const text = result.status === 401 ? t('login.error.origin') : t('login.error.exchange', errorText(result.status, result.data));
    return showLogin({ error: true, text });
  }
  const session = sanitizeSession(result.data);
  if (!session) return showLogin({ error: true, text: t('login.error.exchange', 'réponse invalide') });
  saveSession(session);
  showApp();
}

// ---------- Page principale ----------
function showApp() {
  stopPreview();
  const search = h('input', { type: 'search', id: 'search', placeholder: t('search.placeholder'), 'aria-label': t('search.label'), autocomplete: 'off', enterkeyhint: 'search', oninput: onSearch });
  ui.main = h('div', { class: 'app' },
    h('header', { class: 'topbar' },
      h('div', { class: 'brand' }, h('img', { src: LOGO, alt: '', width: 34, height: 34 }), h('span', { text: t('app.name') })),
      h('div', { class: 'search' }, h('span', { class: 'icon', 'aria-hidden': 'true', text: '🔍' }), search),
      h('button', { class: 'avatar-btn', id: 'account', 'aria-label': t('menu.open'), 'aria-haspopup': 'true', onclick: openMenu },
        state.session.avatar_url ? avatar(state.session.avatar_url, 44) : h('span', { class: 'initial', text: (state.session.username || '?').slice(0, 1).toUpperCase() }))),
    (ui.tabs = h('nav', { class: 'tabs', role: 'tablist', 'aria-label': t('tabs.label') })),
    (ui.content = h('main', { class: 'content' })),
    h('footer', { class: 'dock' },
      (ui.voice = h('div', { class: 'voice' })),
      h('button', { class: 'btn', id: 'join', 'aria-label': t('voice.join'), title: t('voice.join'), onclick: joinVoice }, h('span', { 'aria-hidden': 'true', text: '🔊' }), h('span', { class: 'label', text: t('voice.join') })),
      h('button', { class: 'btn danger', id: 'stop', onclick: stopAll, text: `⏹ ${t('stop')}` })));
  ui.search = search;
  ui.search.value = state.query;
  root.replaceChildren(ui.main);
  renderVoice();
  if (state.load === 'ready') renderAll(); else loadData();
  startPolling();
}

async function loadData() {
  state.load = 'loading';
  renderContent();
  const [sounds, guilds, status] = await Promise.all([api('/sounds'), api('/my-guilds'), api('/status?strict=1')]);
  if (!state.session) return;   // déconnecté pendant le chargement (session expirée)
  if (!sounds.ok || !guilds.ok) {
    state.load = 'error';
    state.loadError = sounds.offline || guilds.offline ? t('offline') : errorText(sounds.status || guilds.status, sounds.data || guilds.data);
    return renderContent();
  }
  state.sounds = dedupeById(sounds.data.sounds || []);
  state.guilds = guilds.data.guilds || [];
  if (status.ok) state.voice = voiceSummary(status.data);
  const sharedLists = await Promise.all(state.guilds.map((guild) => api(`/shared-categories?guild_id=${encodeURIComponent(guild.id)}`)));
  state.shared = {};
  state.guilds.forEach((guild, index) => { if (sharedLists[index].ok) state.shared[guild.id] = sharedLists[index].data.sound_ids || []; });
  state.load = 'ready';
  renderAll();
}

function renderAll() {
  if (!ui.main) return;
  renderTabs();
  renderContent();
  renderVoice();
}

function tabList() {
  return [
    { key: 'favorites', label: t('tabs.favorites'), icon: '★' },
    { key: 'all', label: t('tabs.all') },
    ...state.guilds.map((guild) => ({ key: guild.id, label: guild.name, iconUrl: guild.icon_url })),
  ];
}

function renderTabs() {
  if (!state.guilds.some((guild) => guild.id === state.tab) && !['all', 'favorites'].includes(state.tab)) state.tab = 'all';
  ui.tabs.replaceChildren(...tabList().map((tab) => h('button', {
    class: 'tab', role: 'tab', 'aria-selected': String(state.tab === tab.key && !state.query), onclick: () => { state.tab = tab.key; state.query = ''; ui.search.value = ''; renderTabs(); renderContent(); },
  }, tab.iconUrl ? avatar(tab.iconUrl, 22) : null, tab.icon ? h('span', { 'aria-hidden': 'true', text: tab.icon }) : null, h('span', { text: tab.label }))));
}

function currentView() {
  const all = state.sounds;
  if (state.query.trim()) return { flat: sortByName(filterSounds(all, state.query)), empty: t('empty.search', state.query.trim()) };
  if (state.tab === 'favorites') return { flat: favoriteSounds(all, state.favorites), empty: t('empty.favorites') };
  const sections = buildSections(all, state.guilds, state.shared).filter((section) => state.tab === 'all' || section.key === state.tab);
  return { sections, empty: t('empty.none') };
}

function renderContent() {
  if (!ui.content) return;
  ui.cards = new Map();
  if (state.load === 'loading' || state.load === 'idle') return ui.content.replaceChildren(h('p', { class: 'state-msg', text: t('loading') }));
  if (state.load === 'error') {
    return ui.content.replaceChildren(h('div', { class: 'state-msg' }, h('p', { text: `${t('load.error')} ${state.loadError || ''}` }),
      h('button', { class: 'btn primary', onclick: () => { state.load = 'idle'; loadData(); }, text: t('retry') })));
  }
  const view = currentView();
  const nodes = [];
  if (view.flat) {
    nodes.push(view.flat.length ? h('div', { class: 'grid' }, view.flat.map(makeCard)) : h('p', { class: 'empty', text: view.empty }));
  } else if (view.sections.length === 0) {
    nodes.push(h('p', { class: 'empty', text: view.empty }));
  } else {
    for (const section of view.sections) {
      nodes.push(
        h('h2', { class: 'section-title' }, section.guild ? avatar(section.guild.icon_url, 24) : null, h('span', { text: section.guild ? section.guild.name : t('section.other') })),
        h('div', { class: 'grid' }, section.sounds.map(makeCard)));
    }
  }
  ui.content.replaceChildren(...nodes);
  refreshPlaying();
}

function makeCard(sound) {
  const card = h('div', { class: 'card', vars: { '--c': colorFor(sound, state.colors) }, 'data-id': sound.id },
    h('button', { class: 'card-main', title: sound.name, 'aria-label': t('sound.play', sound.name), onclick: () => play(sound, card) },
      h('span', { class: 'emoji', 'aria-hidden': 'true', text: sound.emoji || '🔊' }),
      h('span', { class: 'name', text: sound.name }),
      state.favorites.has(sound.id) ? h('span', { class: 'fav-badge', 'aria-hidden': 'true', text: '★' }) : null),
    h('button', { class: 'card-more', 'aria-label': t('sound.more', sound.name), onclick: () => openSheet(sound), text: '⋯' }),
    h('span', { class: 'who' }));
  if (!ui.cards.has(sound.id)) ui.cards.set(sound.id, []);
  ui.cards.get(sound.id).push(card);
  return card;
}

/** Allume les boutons des sons en cours de lecture dans votre salon, avec l'avatar de qui les a lancés. */
function refreshPlaying() {
  if (!ui.cards) return;
  for (const [id, cards] of ui.cards) {
    const users = state.playing.get(id) || [];
    const signature = users.map((u) => `${u.user_id}|${u.avatar_url || ''}`).join(',');   // l'avatar compte : l'allumage immédiat n'a pas encore le vrai
    for (const card of cards) {
      card.classList.toggle('is-playing', users.length > 0);
      const who = card.querySelector('.who');
      if (who.dataset.sig === signature) continue;
      who.dataset.sig = signature;
      who.replaceChildren(...users.slice(0, 3).map((u) => avatar(u.avatar_url, 22)).filter(Boolean));
    }
  }
}

function renderVoice() {
  if (!ui.voice) return;
  const voice = state.voice;
  ui.voice.className = `voice ${voice.connected ? 'on' : ''}`;
  const text = voice.connected
    ? (voice.guildName ? t('voice.inGuild', voice.channel || '?', voice.guildName) : t('voice.in', voice.channel || '?'))
    : t('voice.none');
  ui.voice.replaceChildren(h('span', { class: 'dot', 'aria-hidden': 'true' }), h('span', { text }));
}

// ---------- Actions ----------
async function play(sound, card) {
  card.classList.add('bump');
  setTimeout(() => card.classList.remove('bump'), 150);
  if (navigator.vibrate) navigator.vibrate(12);
  const result = await api('/play', { method: 'POST', body: { id: sound.id } });
  if (result.offline) return toast(t('offline'), 'error');
  if (!result.ok) {
    toast(errorText(result.status, result.data), 'error');
    return void tickStatus();
  }
  // Allumage immédiat ; le sondage d'activité (1 s) prend ensuite le relais avec l'état réel du serveur.
  state.playing.set(sound.id, [{ user_id: state.session.user_id, username: state.session.username, avatar_url: state.session.avatar_url }]);
  refreshPlaying();
}

async function stopAll() {
  const result = await api('/stop', { method: 'POST', body: {} });
  if (result.offline) return toast(t('offline'), 'error');
  if (!result.ok) return toast(errorText(result.status, result.data), 'error');
  state.playing = new Map();
  refreshPlaying();
  toast(t('stop.done'), 'success');
}

async function joinVoice() {
  const result = await api('/join-my-channel', { method: 'POST' });
  if (result.offline) return toast(t('offline'), 'error');
  if (!result.ok) return toast(errorText(result.status, result.data), 'error');
  toast(t('voice.joined', result.data.channel_name || ''), 'success');
  tickStatus();
}

function onSearch(event) {
  state.query = event.target.value;
  renderTabs();
  renderContent();
}

// ---------- Sondages (seulement tant que la page est visible) ----------
async function tickStatus() {
  if (busy.status || !state.session) return;
  busy.status = true;
  try {
    const result = await api('/status?strict=1');
    if (result.ok) { state.voice = voiceSummary(result.data); renderVoice(); }
  } finally { busy.status = false; }
}

async function tickActivity() {
  if (busy.activity || !state.session || state.load !== 'ready') return;
  busy.activity = true;
  try {
    const query = state.voice.guildId ? `?guild_id=${encodeURIComponent(state.voice.guildId)}` : '';
    const result = await api(`/activity${query}`);
    if (result.ok) { state.playing = playingMap(result.data); refreshPlaying(); }
  } finally { busy.activity = false; }
}

function startPolling() {
  stopPolling();
  if (document.visibilityState !== 'visible') return;
  timers.status = setInterval(tickStatus, 4000);
  timers.activity = setInterval(tickActivity, 1000);
  tickActivity();
}

function stopPolling() {
  clearInterval(timers.status);
  clearInterval(timers.activity);
  timers = {};
}

document.addEventListener('visibilitychange', () => {
  if (!state.session || !ui.main) return;
  if (document.visibilityState === 'visible') { tickStatus(); startPolling(); } else stopPolling();
});
window.addEventListener('offline', () => toast(t('offline'), 'error'));
window.addEventListener('online', () => { toast(t('online'), 'success'); if (state.session && state.load === 'error') { state.load = 'idle'; loadData(); } });

// ---------- Feuille d'options d'un son ----------
let overlayOpener = null;

function closeOverlay() {
  document.querySelectorAll('.overlay, .menu-layer').forEach((el) => el.remove());
  if (overlayOpener && overlayOpener.isConnected) overlayOpener.focus();
  overlayOpener = null;
}

function openOverlay(node) {
  closeOverlay();
  overlayOpener = document.activeElement;
  document.body.append(node);
  const first = node.querySelector('button');
  if (first) first.focus();
}

function openSheet(sound) {
  const isFavorite = state.favorites.has(sound.id);
  const chosen = state.colors[sound.id];
  const swatch = (color) => h('button', {
    class: `swatch ${color ? '' : 'auto'}`, vars: color ? { '--c': color } : {}, 'aria-label': color || t('sheet.color.auto'),
    'aria-pressed': String((chosen || null) === color), onclick: () => setColor(sound, color),
  });
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': sound.name },
    h('h2', { vars: { '--c': colorFor(sound, state.colors) } }, h('span', { class: 'emoji', 'aria-hidden': 'true', text: sound.emoji || '🔊' }), h('span', { text: sound.name })),
    h('button', { class: 'btn row', onclick: () => { toggleFavorite(sound); closeOverlay(); }, text: `${isFavorite ? '☆' : '★'} ${t(isFavorite ? 'sheet.favorite.remove' : 'sheet.favorite.add')}` }),
    h('button', { class: 'btn row', onclick: () => { closeOverlay(); previewSound(sound); }, text: `🔈 ${t('sheet.preview')}` }),
    h('div', { class: 'label', text: t('sheet.color') }),
    h('div', { class: 'swatches' }, swatch(null), PALETTE.map((color) => swatch(color))),
    h('button', { class: 'btn row', onclick: closeOverlay, text: t('sheet.close') }));
  const overlay = h('div', { class: 'overlay', onclick: (event) => { if (event.target === overlay) closeOverlay(); } }, sheet);
  openOverlay(overlay);
}

function toggleFavorite(sound) {
  state.favorites = toggleInSet(state.favorites, sound.id);
  saveJson(storage, KEYS.favorites, [...state.favorites]);
  renderContent();
}

function setColor(sound, color) {
  if (color) state.colors[sound.id] = color; else delete state.colors[sound.id];
  saveJson(storage, KEYS.colors, state.colors);
  closeOverlay();
  renderContent();
}

// ---------- Écoute sur cet appareil ----------
function stopPreview() {
  if (!preview) return;
  preview.audio.pause();
  URL.revokeObjectURL(preview.url);
  preview = null;
}

async function previewSound(sound) {
  stopPreview();
  let response;
  try {
    response = await fetch(`/sounds/${encodeURIComponent(sound.id)}/file`, { headers: { Authorization: `Bearer ${state.session.session_token}` }, credentials: 'omit' });
  } catch { return toast(t('offline'), 'error'); }
  if (response.status === 401) return endSession('login.expired');
  if (!response.ok) return toast(t('preview.error', `HTTP ${response.status}`), 'error');
  const url = URL.createObjectURL(await response.blob());
  const audio = new Audio(url);
  preview = { audio, url };
  const range = trimRange(sound);
  if (range) {
    audio.addEventListener('loadedmetadata', () => { audio.currentTime = range.start; });
    audio.addEventListener('timeupdate', () => { if (audio.currentTime >= range.end) stopPreview(); });
  }
  audio.addEventListener('ended', stopPreview);
  try { await audio.play(); } catch (error) { stopPreview(); toast(t('preview.error', error.name || 'audio'), 'error'); }
}

// ---------- Menu du compte ----------
function openMenu() {
  const session = state.session;
  const items = [
    h('div', { class: 'who-am-i' }, avatar(session.avatar_url, 36), h('span', { text: session.username || session.user_id })),
    h('button', { class: 'item', onclick: () => { closeOverlay(); toggleLanguage(); }, text: t('menu.language') }),
    state.installPrompt ? h('button', { class: 'item', onclick: installApp, text: t('menu.install') }) : null,
    h('button', { class: 'item', onclick: () => { closeOverlay(); logout(); }, text: t('menu.logout') }),
    h('div', { class: 'about', text: t('menu.about', (state.config && state.config.version) || '') }),
  ];
  const layer = h('div', { class: 'menu-layer', onclick: (event) => { if (event.target === layer) closeOverlay(); } }, h('div', { class: 'menu', role: 'menu' }, items));
  openOverlay(layer);
}

function toggleLanguage() {
  lang = lang === 'fr' ? 'en' : 'fr';
  storage.setItem(KEYS.lang, lang);
  t = makeTranslator(lang);
  document.documentElement.lang = lang;
  if (state.session && ui.main) showApp(); else showLogin(null);
}

async function installApp() {
  const prompt = state.installPrompt;
  state.installPrompt = null;
  closeOverlay();
  if (prompt) await prompt.prompt();
}

window.addEventListener('beforeinstallprompt', (event) => { event.preventDefault(); state.installPrompt = event; });
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeOverlay();
  else if (event.key === '/' && ui.search && document.activeElement === document.body) { event.preventDefault(); ui.search.focus(); }
});

// ---------- Démarrage ----------
async function boot() {
  document.documentElement.lang = lang;
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => { /* pas de service worker : la page marche quand même */ });
  }
  fetchConfig().catch(() => { /* sera réessayé à la connexion */ });
  if (window.location.pathname === '/app/callback') return handleCallback();
  if (state.session) return showApp();
  showLogin(null);
}

boot();
