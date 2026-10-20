// WaseBoard web : l'interface. Toute la logique testable est dans logic.js ; ici, le navigateur (DOM, réseau, stockage).
// Règles : jamais de HTML construit à partir de données (les noms de sons et pseudos sont du texte), aucun style en ligne (CSP),
// l'API est celle de l'application Windows, authentifiée par la session Discord en « Authorization: Bearer ».
import { avatar, closeAllOverlays, closeOverlay, h, icon, openOverlay, toast } from './dom.js';
import { createCategoryUi } from './categories-ui.js';
import { audioFiles, uploadableGuilds } from './editor-logic.js';
import { createEditor } from './editor.js';
import { LANGUAGES, makeTranslator, pickLanguage } from './i18n.js';
import { createPrefsStore } from './prefs.js';
import {
  PALETTE, PALETTES, activeUserIds, buildAuthorizeUrl, buildSections, categoryTabs, colorFor, dedupeById, emptyPrefs, errorText, favoriteSounds, filterSounds,
  isActivityLocation, loadJson, makeState, nowPlayingEntries, parseCallback, personalKey, playingMap, sanitizeColors, sanitizeIds, sanitizePalette, sanitizeSession,
  saveJson, setColor as setPrefsColor, sortByName, toggleFavorite as toggleFavoritePrefs, toggleInSet, trimRange, voiceSummary,
} from './logic.js';

const KEYS = { session: 'wb.session', favorites: 'wb.favorites', colors: 'wb.colors', lang: 'wb.lang', palette: 'wb.palette', oauthState: 'wb.oauth_state' };
const collapsedKey = (userId) => `wb.collapsed.${userId}`;   // sections repliées : un choix d'affichage propre à cet appareil
const navClosedKey = (userId) => `wb.navclosed.${userId}`;   // groupes repliés de la barre latérale (Serveurs, Catégories) : idem
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
let palette = sanitizePalette(storage.getItem(KEYS.palette));   // palette de couleurs (comme « Apparence » de l'application Windows), propre à ce navigateur
function applyPalette() {
  document.documentElement.dataset.palette = palette;
  const dark = PALETTES.find((entry) => entry.id === palette).swatch[0];
  document.querySelector('meta[name="theme-color"][media*="dark"]')?.setAttribute('content', dark);   // barre d'adresse de l'application installée
}
applyPalette();
const state = {
  config: null,
  session: sanitizeSession(loadJson(storage, KEYS.session, null)),
  sounds: [], guilds: [], shared: {},
  prefs: emptyPrefs(),                // favoris, couleurs et catégories personnelles, partagés avec l'application Windows (voir prefs.js)
  loadedAt: 0,
  voice: voiceSummary(null),
  playing: new Map(),
  tab: 'all', query: '',
  collapsed: new Set(),               // clés des sections repliées (onglet « Tous »), gardées dans ce navigateur
  navClosed: new Set(),               // groupes repliés de la barre latérale
  favorites: new Set(),               // dérivés de prefs (voir applyPrefs)
  colors: {},
  load: 'idle', // idle | loading | error | ready
  installPrompt: null,
};
const ui = {};            // éléments de la page principale, quand elle est affichée
let timers = {};
let busy = { status: false, activity: false };
let preview = null;       // { audio, url } : écoute locale en cours

const root = document.getElementById('root');

// Activité Discord : la page est chargée dans une iframe de Discord (voir activity.js). Le serveur et le salon viennent alors de Discord.
const inActivity = isActivityLocation(window.location.search);
if (inActivity) document.documentElement.classList.add('activity');
state.activity = null;   // { connection, guildId, channelId }

/** Serveur visé par les actions : celui de l'Activité, sinon celui du salon vocal où vous êtes (déduit par le serveur). */
function targetGuildId() {
  return (state.activity && state.activity.guildId) || state.voice.guildId || null;
}
const guildQuery = (prefix) => (targetGuildId() ? `${prefix}guild_id=${encodeURIComponent(targetGuildId())}` : '');

// ---------- Réseau ----------
async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {};
  if (state.session) headers.Authorization = `Bearer ${state.session.session_token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let payload;
  if (form) payload = form;                       // FormData : le navigateur pose lui-même le Content-Type (avec sa frontière)
  else if (body !== undefined) payload = JSON.stringify(body);
  let response;
  try {
    response = await fetch(path, { method, headers, credentials: 'omit', cache: 'no-store', body: payload });
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
  if (prefsStore) prefsStore.stop();
  prefsStore = null;
  state.collapsed = new Set();
  state.navClosed = new Set();
  state.prefs = emptyPrefs();
  state.favorites = new Set();
  state.colors = {};
  stopPolling();
  stopPreview();
  closeAllOverlays();
  if (state.activity) { activityRelogin(); return; }
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

// ---------- Préférences synchronisées (favoris, couleurs, catégories personnelles) ----------
let prefsStore = null;

function applyPrefs() {
  state.prefs = prefsStore.prefs;
  state.favorites = new Set(state.prefs.favorites);
  state.colors = state.prefs.colors;
}

function onPrefsChange() {
  applyPrefs();
  if (ui.main) renderAll();
}

/** Crée le magasin de la personne connectée (une fois). Favoris et couleurs d'avant la synchronisation, gardés seuls dans ce navigateur,
 *  sont repris à la première synchronisation puis oubliés. */
function ensurePrefs() {
  if (prefsStore || !state.session) return;
  const legacy = { favorites: [...sanitizeIds(loadJson(storage, KEYS.favorites, []))], colors: sanitizeColors(loadJson(storage, KEYS.colors, {})) };
  prefsStore = createPrefsStore({
    api, storage, userId: state.session.user_id, legacy, onChange: onPrefsChange,
    onFirstSync: () => { storage.removeItem(KEYS.favorites); storage.removeItem(KEYS.colors); },
  });
  applyPrefs();
  state.collapsed = sanitizeIds(loadJson(storage, collapsedKey(state.session.user_id), []));
  state.navClosed = sanitizeIds(loadJson(storage, navClosedKey(state.session.user_id), []));
}

const syncPrefs = () => { if (prefsStore) prefsStore.sync(); };

const categoryUi = createCategoryUi({
  t: (key, ...args) => t(key, ...args),
  getState: () => state,
  edit: (change) => { if (prefsStore) prefsStore.edit(change); },
});

// ---------- Page principale ----------
/** Bouton de la barre d'outils : icône (la couleur vient du texte) et, en option, libellé. */
function toolButton(props, iconName, label) {
  return h('button', { type: 'button', ...props, class: `tool ${props.class || ''}` }, icon(iconName), label ? h('span', { class: 'label', text: label }) : null);
}

/** Barre latérale (grands écrans) : accueil et favoris, serveurs et catégories, « en ce moment », état, paramètres. */
function buildSidebar() {
  ui.navHome = h('button', { type: 'button', class: 'btn nav-home', onclick: () => selectTab('all'), text: t('nav.home') });
  ui.navFav = h('button', { type: 'button', class: 'btn nav-fav', onclick: () => selectTab('favorites'), text: t('nav.favorites') });
  ui.members = h('div', { class: 'members', role: 'list', 'aria-label': t('members.label'), title: t('members.label') });
  ui.navList = h('div', { class: 'side-nav' });
  ui.nowList = h('div', { class: 'now-list', 'aria-live': 'polite' });
  ui.status = h('div', { class: 'status' });
  ui.sideVoice = h('div', { class: 'side-voice' });
  return h('aside', { class: 'sidebar', 'aria-label': t('nav.label') },
    h('div', { class: 'side-head' }, h('img', { src: LOGO, alt: '', width: 28, height: 28 }), h('h1', { text: t('app.name') })),
    ui.members,
    h('div', { class: 'nav-top' }, ui.navHome, ui.navFav),
    ui.navList,
    h('div', { class: 'side-now' }, h('h2', { text: t('now.title') }), ui.nowList),
    h('div', { class: 'side-foot' }, ui.status, ui.sideVoice,
      toolButton({ class: 'settings-btn', id: 'settings', onclick: openSettings }, 'gear', t('settings.title'))));
}

function showApp() {
  stopPreview();
  ensurePrefs();
  const search = h('input', { type: 'search', id: 'search', placeholder: t('search.placeholder'), 'aria-label': t('search.label'), autocomplete: 'off', enterkeyhint: 'search', oninput: onSearch });
  ui.main = h('div', { class: 'app' },
    buildSidebar(),
    h('div', { class: 'main' },
      h('header', { class: 'toolbar' },
        h('div', { class: 'brand' }, h('img', { src: LOGO, alt: '', width: 34, height: 34 }), h('span', { text: t('app.name') })),
        h('div', { class: 'search' }, icon('search'), search),
        h('div', { class: 'tool-actions' },
          toolButton({ class: 'wide-only', id: 'refresh', title: t('tool.refresh'), 'aria-label': t('tool.refresh'), onclick: refreshNow }, 'refresh'),
          toolButton({ class: 'wide-only tool-join', id: 'join', title: t('voice.join'), onclick: joinVoice }, 'speaker', t('tool.voice')),
          toolButton({ class: 'wide-only tool-stop', id: 'stop', onclick: stopAll }, 'stop', t('stop')),
          toolButton({ class: 'wide-only', id: 'new-category', title: t('tabs.newCategory'), onclick: newCategory }, 'tag', t('tool.category')),
          (ui.addButton = h('button', { type: 'button', class: 'btn primary add-btn', id: 'add', hidden: true, 'aria-label': t('menu.add'), title: t('menu.add'), onclick: () => ui.fileInput.click() },
            icon('plus'), h('span', { class: 'label', text: t('menu.add') }))),
          (ui.fileInput = h('input', { type: 'file', multiple: true, hidden: true, accept: 'audio/*,.mp3,.wav,.ogg,.flac,.m4a,.wma', onchange: onFilesChosen })),
          h('button', { type: 'button', class: 'avatar-btn', id: 'account', 'aria-label': t('menu.open'), 'aria-haspopup': 'true', onclick: openSettings },
            state.session.avatar_url ? avatar(state.session.avatar_url, 44) : h('span', { class: 'initial', text: (state.session.username || '?').slice(0, 1).toUpperCase() })))),
      (ui.tabs = h('nav', { class: 'tabs', role: 'tablist', 'aria-label': t('tabs.label') })),
      (ui.content = h('main', { class: 'content' })),
      h('footer', { class: 'dock' },
        (ui.voice = h('div', { class: 'voice' })),
        toolButton({ class: 'tool-join', id: 'join-dock', 'aria-label': t('voice.join'), title: t('voice.join'), onclick: joinVoice }, 'speaker', t('voice.join')),
        toolButton({ class: 'tool-stop solid', id: 'stop-dock', onclick: stopAll }, 'stop', t('stop')))));
  ui.search = search;
  ui.search.value = state.query;
  root.replaceChildren(ui.main);
  renderVoice();
  renderNow();
  renderStatus();
  if (state.load === 'ready') renderAll(); else loadData();
  startPolling();
}

/** Catalogue, serveurs, catégories partagées et état vocal ; { error } si le chargement échoue, null si la session a pris fin. */
async function fetchCatalog() {
  const [sounds, guilds, status] = await Promise.all([api('/sounds'), api('/my-guilds'), api(`/status?strict=1${guildQuery('&')}`)]);
  if (!state.session) return null;   // déconnecté pendant le chargement (session expirée)
  if (!sounds.ok || !guilds.ok) {
    return { error: sounds.offline || guilds.offline ? t('offline') : errorText(sounds.status || guilds.status, sounds.data || guilds.data) };
  }
  const guildList = guilds.data.guilds || [];
  const sharedLists = await Promise.all(guildList.map((guild) => api(`/shared-categories?guild_id=${encodeURIComponent(guild.id)}`)));
  const shared = {};
  guildList.forEach((guild, index) => { if (sharedLists[index].ok) shared[guild.id] = sharedLists[index].data.sound_ids || []; });
  return { sounds: dedupeById(sounds.data.sounds || []), guilds: guildList, shared, voice: status.ok ? voiceSummary(status.data) : null };
}

function applyCatalog(catalog) {
  state.sounds = catalog.sounds;
  state.guilds = catalog.guilds;
  state.shared = catalog.shared;
  if (catalog.voice) state.voice = catalog.voice;
  state.load = 'ready';
  state.loadedAt = Date.now();
}

async function loadData() {
  state.load = 'loading';
  renderContent();
  const catalog = await fetchCatalog();
  if (!catalog) return;
  if (catalog.error) {
    state.load = 'error';
    state.loadError = catalog.error;
    return renderContent();
  }
  applyCatalog(catalog);
  renderAll();
  syncPrefs();   // récupère ce qui a été changé ailleurs (application Windows, autre appareil)
}

/** Bouton « actualiser » de la barre d'outils : relit le catalogue et le dit. */
async function refreshNow() {
  const catalog = await fetchCatalog();
  if (!catalog) return;
  if (catalog.error) return toast(catalog.error, 'error');
  applyCatalog(catalog);
  renderAll();
  toast(t('refresh.done'), 'success');
}

/** Recharge le catalogue après un ajout, une modification ou une suppression (sans écran de chargement). */
async function refreshCatalog() {
  const catalog = await fetchCatalog();
  if (!catalog || catalog.error) return;
  applyCatalog(catalog);
  renderAll();
}

function renderAll() {
  if (!ui.main) return;
  renderTabs();
  renderContent();
  renderVoice();
  renderNow();
  ui.addButton.hidden = uploadableGuilds(state.guilds).length === 0;   // aucun serveur où l'on peut ajouter : pas de bouton
}

function tabList() {
  return [
    { key: 'favorites', label: t('tabs.favorites'), icon: '★' },
    { key: 'all', label: t('tabs.all') },
    ...categoryTabs(state.sounds, state.guilds, state.prefs).map((tab) => (tab.type === 'guild'
      ? { key: tab.key, label: tab.guild.name, iconUrl: tab.guild.icon_url, group: 'servers' }
      : { key: tab.key, label: tab.name, icon: '📁', group: 'categories' })),
  ];
}

/** Choisit ce qui est affiché (accueil, favoris, un serveur ou une catégorie) et efface la recherche. */
function selectTab(key) {
  state.tab = key;
  state.query = '';
  if (ui.search) ui.search.value = '';
  renderTabs();
  renderContent();
}

function renderTabs() {
  const tabs = tabList();
  if (!tabs.some((tab) => tab.key === state.tab)) state.tab = 'all';
  ui.tabs.replaceChildren(...tabs.map((tab) => h('button', {
    class: 'tab', role: 'tab', 'aria-selected': String(state.tab === tab.key && !state.query), onclick: () => selectTab(tab.key),
  }, tab.iconUrl ? avatar(tab.iconUrl, 22) : null, tab.icon ? h('span', { 'aria-hidden': 'true', text: tab.icon }) : null, h('span', { text: tab.label }))),
  h('button', { class: 'tab add-category', id: 'new-category-tab', title: t('tabs.newCategory'), 'aria-label': t('tabs.newCategory'), onclick: newCategory },
    h('span', { 'aria-hidden': 'true', text: '📁＋' })));
  // Avec beaucoup d'onglets (serveurs + catégories personnelles), celui qu'on vient de choisir peut être hors de vue sur un petit écran.
  ui.tabs.querySelector('[aria-selected="true"]')?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  renderNav(tabs);
}

/** Barre latérale : accueil / favoris, puis les groupes « Serveurs » et « Catégories » (repliables, comme dans l'application). */
function renderNav(tabs = tabList()) {
  if (!ui.navList) return;
  const current = state.query ? null : state.tab;
  for (const [button, key] of [[ui.navHome, 'all'], [ui.navFav, 'favorites']]) {
    button.classList.toggle('active', current === key);
    button.setAttribute('aria-pressed', String(current === key));
  }
  const group = (id, titleKey) => {
    const items = tabs.filter((tab) => tab.group === id);
    if (items.length === 0) return null;
    const closed = state.navClosed.has(id);
    const title = t(titleKey);
    return h('div', { class: `nav-group${closed ? ' closed' : ''}` },
      h('button', { type: 'button', class: 'nav-group-head', 'aria-expanded': String(!closed), 'aria-label': t('nav.toggle', title), onclick: () => toggleNavGroup(id) },
        h('span', { class: 'chevron', 'aria-hidden': 'true', text: '▾' }), h('span', { class: 'nav-group-title', text: title }), h('span', { class: 'nav-count', text: String(items.length) })),
      closed ? null : items.map((tab) => h('button', {
        type: 'button', class: 'nav-item', title: tab.label, 'aria-current': String(current === tab.key), onclick: () => selectTab(tab.key),
      }, tab.iconUrl ? avatar(tab.iconUrl, 16) : h('span', { class: 'nav-icon', 'aria-hidden': 'true', text: tab.icon }), h('span', { class: 'nav-label', text: tab.label }))));
  };
  ui.navList.replaceChildren(...[group('servers', 'nav.servers'), group('categories', 'nav.categories')].filter(Boolean));
}

function toggleNavGroup(id) {
  state.navClosed = toggleInSet(state.navClosed, id);
  saveJson(storage, navClosedKey(state.session.user_id), [...state.navClosed]);
  renderNav();
}

/** Crée une catégorie (nom demandé) et l'affiche. */
async function newCategory() {
  const name = await categoryUi.createCategory();
  if (!name) return;
  selectTab(personalKey(name));
}

function currentView() {
  const all = state.sounds;
  if (state.query.trim()) return { flat: sortByName(filterSounds(all, state.query)), empty: t('empty.search', state.query.trim()) };
  if (state.tab === 'favorites') return { flat: favoriteSounds(all, state.favorites), empty: t('empty.favorites') };
  const sections = buildSections(all, state.guilds, state.shared, state.prefs).filter((section) => state.tab === 'all' || section.key === state.tab);
  return { sections, empty: t('empty.none') };
}

/** Replie ou déplie une section de l'onglet « Tous » ; le choix est gardé dans ce navigateur (comme dans l'application, qui garde le sien). */
function toggleSection(key) {
  state.collapsed = toggleInSet(state.collapsed, key);
  saveJson(storage, collapsedKey(state.session.user_id), [...state.collapsed]);
  renderContent();
}

function renderContent() {
  if (!ui.content) return;
  ui.cards = new Map();
  renderStatus();
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
      const personal = section.kind === 'personal';
      const title = personal ? section.name : section.guild ? section.guild.name : t('section.other');
      const foldable = state.tab === 'all';   // dans l'onglet d'une seule section, la replier ne laisserait qu'une page vide
      const folded = foldable && state.collapsed.has(section.key);
      const label = [section.guild ? avatar(section.guild.icon_url, 20) : null, personal ? h('span', { 'aria-hidden': 'true', text: '📁' }) : null,
        h('span', { class: 'section-name', text: title }), folded ? h('span', { class: 'section-count', text: String(section.sounds.length) }) : null];
      nodes.push(
        h('h2', { class: `section-title${folded ? ' folded' : ''}` },
          foldable
            ? h('button', {
              class: 'section-toggle', 'aria-expanded': String(!folded), 'aria-label': t(folded ? 'section.expand' : 'section.collapse', title),
              onclick: () => toggleSection(section.key),
            }, h('span', { class: 'chevron', 'aria-hidden': 'true', text: '▾' }), label)
            : h('span', { class: 'section-toggle static' }, label),
          personal ? h('button', { class: 'icon-btn section-more', 'aria-label': t('category.more', section.name), onclick: () => categoryUi.openCategorySheet(section.name), text: '⋯' }) : null),
        ...(folded ? [] : [personal && section.sounds.length === 0 ? h('p', { class: 'empty-category', text: t('category.empty') }) : h('div', { class: 'grid' }, section.sounds.map(makeCard))]));
    }
  }
  ui.content.replaceChildren(...nodes);
  refreshPlaying();
}

function makeCard(sound) {
  const card = h('div', { class: 'card', vars: { '--c': colorFor(sound, state.colors) }, 'data-id': sound.id, oncontextmenu: (event) => { event.preventDefault(); openSheet(sound); } },
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

/** Allume les boutons des sons en cours de lecture dans votre salon, avec l'avatar de qui les a lancés (le premier, puis « +N », comme l'application). */
function refreshPlaying() {
  renderNow();
  renderMembers();
  if (!ui.cards) return;
  for (const [id, cards] of ui.cards) {
    const users = state.playing.get(id) || [];
    const signature = users.map((u) => `${u.user_id}|${u.avatar_url || ''}`).join(',');   // l'avatar compte : l'allumage immédiat n'a pas encore le vrai
    for (const card of cards) {
      card.classList.toggle('is-playing', users.length > 0);
      const who = card.querySelector('.who');
      if (who.dataset.sig === signature) continue;
      who.dataset.sig = signature;
      who.replaceChildren(...(users.length ? [avatar(users[0].avatar_url, 18) || h('span', { class: 'who-initial', text: (users[0].username || '?').slice(0, 1).toUpperCase() }),
        users.length > 1 ? h('span', { class: 'who-extra', text: `+${users.length - 1}` }) : null].filter(Boolean) : []));
    }
  }
}

/** Voyant et texte du vocal : barre du bas (petits écrans) et pied de la barre latérale (grands écrans). */
function renderVoice() {
  const voice = state.voice;
  const text = voice.connected
    ? (voice.guildName ? t('voice.inGuild', voice.channel || '?', voice.guildName) : t('voice.in', voice.channel || '?'))
    : t('voice.none');
  if (ui.voice) {
    ui.voice.className = `voice ${voice.connected ? 'on' : ''}`;
    ui.voice.replaceChildren(h('span', { class: 'dot', 'aria-hidden': 'true' }), h('span', { text }));
  }
  if (ui.sideVoice) {
    ui.sideVoice.className = `side-voice ${voice.connected ? 'on' : ''}`;
    ui.sideVoice.replaceChildren(h('span', { text: voice.connected ? t('voice.side', voice.channel || '?') : t('voice.none') }));
  }
  renderMembers();
}

/** Avatars des personnes en vocal avec vous ; celui de qui lance un son s'allume en même temps que le bouton. */
function renderMembers() {
  if (!ui.members) return;
  const members = state.voice.members || [];
  const signature = members.map((m) => `${m.user_id}|${m.avatar_url || ''}`).join(',');
  if (ui.members.dataset.sig !== signature) {
    ui.members.dataset.sig = signature;
    ui.members.replaceChildren(...members.map((m) => h('span', { class: 'member', role: 'listitem', 'data-user': m.user_id, title: m.username },
      avatar(m.avatar_url, 22) || h('span', { class: 'member-initial', text: (m.username || '?').slice(0, 1).toUpperCase() }))));
  }
  const active = activeUserIds(state.playing);
  for (const node of ui.members.children) node.classList.toggle('active', active.has(node.dataset.user));
}

/** « En ce moment » : qui joue quoi, d'après l'activité du serveur (même source que l'allumage des boutons). */
function renderNow() {
  if (!ui.nowList) return;
  const entries = nowPlayingEntries(state.playing, state.sounds);
  const signature = `${state.sounds.length}|${entries.map((e) => `${e.key}|${e.user.avatar_url || ''}`).join(',')}`;
  if (ui.nowList.dataset.sig === signature) return;
  ui.nowList.dataset.sig = signature;
  ui.nowList.replaceChildren(...(entries.length
    ? entries.map((e) => h('div', { class: 'now-item' },
      avatar(e.user.avatar_url, 20) || h('span', { class: 'now-initial', text: (e.user.username || '?').slice(0, 1).toUpperCase() }),
      h('span', { class: 'now-text' }, h('b', { text: e.user.username || '?' }), t('now.plays'), `${e.emoji} ${e.soundName}`)))
    : [h('p', { class: 'now-none', text: t('now.none') })]));
}

/** Pastille et texte « Connecté — N son(s) » (pied de la barre latérale). */
function renderStatus() {
  if (!ui.status) return;
  const ready = state.load === 'ready';
  const failed = state.load === 'error';
  ui.status.className = `status ${ready ? 'on' : failed ? 'off' : ''}`;
  ui.status.replaceChildren(h('span', { class: 'dot', 'aria-hidden': 'true' }),
    h('span', { text: ready ? t('status.connected', state.sounds.length) : failed ? t('status.offline') : t('loading') }));
}

// ---------- Actions ----------
async function play(sound, card) {
  card.classList.add('bump');
  setTimeout(() => card.classList.remove('bump'), 150);
  if (navigator.vibrate) navigator.vibrate(12);
  const result = await api('/play', { method: 'POST', body: { id: sound.id, ...(state.activity ? { guild_id: targetGuildId() } : {}) } });
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
  const result = await api('/stop', { method: 'POST', body: state.activity ? { guild_id: targetGuildId() } : {} });
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
    const result = await api(`/status?strict=1${guildQuery('&')}`);
    if (result.ok) { state.voice = voiceSummary(result.data); renderVoice(); }
  } finally { busy.status = false; }
}

async function tickActivity() {
  if (busy.activity || !state.session || state.load !== 'ready') return;
  busy.activity = true;
  try {
    const result = await api(`/activity${guildQuery('?')}`);
    if (result.ok) { state.playing = playingMap(result.data); refreshPlaying(); }
  } finally { busy.activity = false; }
}

function startPolling() {
  stopPolling();
  if (document.visibilityState !== 'visible') return;
  timers.status = setInterval(tickStatus, 4000);
  timers.activity = setInterval(tickActivity, 1000);
  timers.prefs = setInterval(syncPrefs, 30000);   // ce qui a été changé dans l'application Windows apparaît dans la minute
  tickActivity();
}

function stopPolling() {
  clearInterval(timers.status);
  clearInterval(timers.activity);
  clearInterval(timers.prefs);
  timers = {};
}

const RELOAD_AFTER_MS = 60 * 1000;   // une page restée en veille plus longtemps relit le catalogue en revenant (catégories changées ailleurs)
document.addEventListener('visibilitychange', () => {
  if (!state.session || !ui.main) return;
  if (document.visibilityState === 'visible') {
    tickStatus();
    startPolling();
    syncPrefs();
    if (state.load === 'ready' && Date.now() - state.loadedAt > RELOAD_AFTER_MS) refreshCatalog();
  } else stopPolling();
});
window.addEventListener('offline', () => toast(t('offline'), 'error'));
window.addEventListener('online', () => { toast(t('online'), 'success'); syncPrefs(); if (state.session && state.load === 'error') { state.load = 'idle'; loadData(); } });

// ---------- Feuille d'options d'un son ----------
function openSheet(sound) {
  const isFavorite = state.favorites.has(sound.id);
  const chosen = state.colors[sound.id];
  const swatch = (color) => h('button', {
    class: `swatch ${color ? '' : 'auto'}`, vars: color ? { '--c': color } : {}, 'aria-label': color || t('sheet.color.auto'),
    'aria-pressed': String((chosen || null) === color), onclick: () => setColor(sound, color),
  });
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': sound.name },
    h('h2', { vars: { '--c': colorFor(sound, state.colors) } }, h('span', { class: 'emoji', 'aria-hidden': 'true', text: sound.emoji || '🔊' }), h('span', { text: sound.name })),
    sound.can_edit ? h('button', { class: 'btn row', id: 'edit-sound', onclick: () => { closeOverlay(); editor.openEdit(sound); }, text: `✏️ ${t('sheet.edit')}` }) : null,
    h('button', { class: 'btn row', onclick: () => { toggleFavorite(sound); closeOverlay(); }, text: `${isFavorite ? '☆' : '★'} ${t(isFavorite ? 'sheet.favorite.remove' : 'sheet.favorite.add')}` }),
    h('button', { class: 'btn row', id: 'sheet-categories', onclick: () => { closeOverlay(); categoryUi.openCategoryPicker(sound); }, text: `📁 ${t('sheet.categories')}` }),
    h('button', { class: 'btn row', onclick: () => { closeOverlay(); previewSound(sound); }, text: `🔈 ${t('sheet.preview')}` }),
    h('div', { class: 'label', text: t('sheet.color') }),
    h('div', { class: 'swatches' }, swatch(null), PALETTE.map((color) => swatch(color))),
    h('button', { class: 'btn row', onclick: closeOverlay, text: t('sheet.close') }));
  const overlay = h('div', { class: 'overlay', onclick: (event) => { if (event.target === overlay) closeOverlay(); } }, sheet);
  openOverlay(overlay);
}

function toggleFavorite(sound) {
  if (prefsStore) prefsStore.edit((prefs) => toggleFavoritePrefs(prefs, sound.id));
}

function setColor(sound, color) {
  closeOverlay();
  if (prefsStore) prefsStore.edit((prefs) => setPrefsColor(prefs, sound.id, color));
}

// ---------- Ajout de sons : sélecteur de fichiers et glisser-déposer ----------
function addFiles(files) {
  const accepted = audioFiles(files);
  if (accepted.length === 0) return toast(t('drop.none'), 'error');
  if (uploadableGuilds(state.guilds).length === 0) return toast(t('editor.error.noGuild'), 'error');
  if (accepted.length < files.length) toast(t('drop.ignored', files.length - accepted.length), 'info');
  editor.openAdd(accepted);
}

function onFilesChosen(event) {
  const files = Array.from(event.target.files || []);
  event.target.value = '';
  if (files.length) addFiles(files);
}

let dragDepth = 0;
function isFileDrag(event) {
  return event.dataTransfer && Array.from(event.dataTransfer.types || []).includes('Files');
}
function dropAllowed() {
  return Boolean(state.session && ui.main && state.load === 'ready' && uploadableGuilds(state.guilds).length > 0);
}
document.addEventListener('dragenter', (event) => {
  if (!isFileDrag(event) || !dropAllowed()) return;
  event.preventDefault();
  if (++dragDepth === 1) document.body.append(h('div', { class: 'drop-overlay', id: 'drop-overlay', text: t('drop.overlay') }));
});
document.addEventListener('dragover', (event) => { if (isFileDrag(event) && dropAllowed()) event.preventDefault(); });
document.addEventListener('dragleave', (event) => {
  if (!isFileDrag(event) || dragDepth === 0) return;
  if (--dragDepth === 0) document.getElementById('drop-overlay')?.remove();
});
document.addEventListener('drop', (event) => {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  dragDepth = 0;
  document.getElementById('drop-overlay')?.remove();
  if (dropAllowed()) addFiles(Array.from(event.dataTransfer.files));
});

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

/** Contenu du fichier d'un son (pour la forme d'onde de l'éditeur) ; null si indisponible. */
async function fetchSoundFile(id) {
  try {
    const response = await fetch(`/sounds/${encodeURIComponent(id)}/file`, { headers: { Authorization: `Bearer ${state.session.session_token}` }, credentials: 'omit' });
    if (response.status === 401) { endSession('login.expired'); return null; }
    return response.ok ? await response.arrayBuffer() : null;
  } catch { return null; }
}

const editor = createEditor({
  t: (key, ...args) => t(key, ...args),
  api,
  fetchFile: fetchSoundFile,
  getState: () => state,
  refresh: refreshCatalog,
  currentTab: () => state.tab,
});

// ---------- Paramètres : compte, palette, langue ----------
function choosePalette(id, row) {
  palette = sanitizePalette(id);
  storage.setItem(KEYS.palette, palette);
  applyPalette();
  for (const button of row.querySelectorAll('.palette')) button.setAttribute('aria-pressed', String(button.dataset.palette === palette));
}

function openSettings() {
  const session = state.session;
  const row = h('div', { class: 'palettes', role: 'group', 'aria-label': t('settings.palette') });
  row.append(...PALETTES.map((entry) => h('button', {
    type: 'button', class: 'palette', 'data-palette': entry.id, 'aria-pressed': String(palette === entry.id), onclick: () => choosePalette(entry.id, row),
  }, h('span', { class: 'palette-dots', 'aria-hidden': 'true' }, h('span', { vars: { '--c': entry.swatch[0] } }), h('span', { vars: { '--c': entry.swatch[1] } })),
  h('span', { class: 'palette-name', text: t(entry.labelKey) }))));
  const sheet = h('div', { class: 'sheet settings', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('settings.title') },
    h('h2', {}, avatar(session.avatar_url, 40) || h('span', { class: 'who-initial big', text: (session.username || '?').slice(0, 1).toUpperCase() }), h('span', { text: session.username || session.user_id })),
    h('div', { class: 'label', text: t('settings.palette') }),
    row,
    h('p', { class: 'hint', text: t('settings.palette.note') }),
    h('button', { class: 'btn row', onclick: () => { closeOverlay(); toggleLanguage(); }, text: `🌐 ${t('menu.language')}` }),
    state.installPrompt && !state.activity ? h('button', { class: 'btn row', onclick: installApp, text: `⬇️ ${t('menu.install')}` }) : null,
    state.activity ? null : h('button', { class: 'btn row', onclick: () => { closeOverlay(); logout(); }, text: `🚪 ${t('menu.logout')}` }),
    h('div', { class: 'about', text: t('menu.about', (state.config && state.config.version) || '') }),
    h('button', { class: 'btn row', onclick: closeOverlay, text: t('sheet.close') }));
  const overlay = h('div', { class: 'overlay', onclick: (event) => { if (event.target === overlay) closeOverlay(); } }, sheet);
  openOverlay(overlay);
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

// ---------- Activité Discord ----------
function showActivityMessage(text, retry = false) {
  root.replaceChildren(h('main', { class: 'login' }, h('div', { class: 'login-card' },
    h('img', { class: 'login-logo', src: LOGO, alt: '', width: 96, height: 96 }),
    h('p', { class: retry ? 'notice error' : '', text }),
    retry ? h('button', { class: 'btn primary', onclick: bootActivity, text: t('retry') }) : null)));
  ui.main = null;
}

/** Autorise la personne auprès de Discord (sans fenêtre si c'est déjà fait), puis échange le code contre une session du serveur. */
async function activityLogin() {
  const { authorizeActivity, authenticateActivity } = await import('./activity.js');
  const config = await fetchConfig();
  const code = await authorizeActivity(state.activity.connection, config);
  const result = await api('/oauth/exchange', { method: 'POST', body: { code } });
  if (!result.ok) throw new Error(result.offline ? t('offline') : errorText(result.status, result.data));
  const session = sanitizeSession(result.data);
  if (!session) throw new Error('réponse invalide');
  saveSession(session);
  if (result.data.discord_access_token) authenticateActivity(state.activity.connection, result.data.discord_access_token);
}

async function activityRelogin() {
  try {
    await activityLogin();
    showApp();
  } catch (error) {
    showActivityMessage(t('activity.error', error.message || String(error)), true);
  }
}

async function bootActivity() {
  showActivityMessage(t('activity.connecting'));
  try {
    const config = await fetchConfig();
    const { connectActivity } = await import('./activity.js');
    const connection = await connectActivity(config);
    state.activity = { connection, guildId: connection.guildId, channelId: connection.channelId };
    if (!state.activity.guildId) return showActivityMessage(t('activity.noGuild'));
    if (!state.session) await activityLogin();
    showApp();
  } catch (error) {
    showActivityMessage(t('activity.error', error.message || String(error)), true);
  }
}

// ---------- Démarrage ----------
async function boot() {
  document.documentElement.lang = lang;
  if (inActivity) return bootActivity();   // dans Discord : pas de service worker ni de page d'installation
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => { /* pas de service worker : la page marche quand même */ });
  }
  fetchConfig().catch(() => { /* sera réessayé à la connexion */ });
  if (window.location.pathname === '/app/callback') return handleCallback();
  if (state.session) return showApp();
  showLogin(null);
}

boot();
