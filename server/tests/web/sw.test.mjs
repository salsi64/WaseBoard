// Service worker (server/web/sw.js) : exécuté tel quel dans un faux environnement (caches, réseau, événements).
// Lancer : node --test server/tests/web/
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const SOURCE = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'web', 'sw.js'), 'utf8');
const ORIGIN = 'https://waseboard.test';

/** Charge sw.js avec un faux `self`, un faux `caches` et un faux réseau ; renvoie de quoi déclencher les événements. */
function load({ build = 'abc123', network = {}, existingCaches = [] } = {}) {
  const store = new Map(existingCaches.map((name) => [name, new Map()]));
  const listeners = {};
  const fetched = [];
  const makeResponse = (body, ok = true) => ({ ok, body, clone() { return { ...this }; } });
  const cacheApi = {
    open: async (name) => {
      if (!store.has(name)) store.set(name, new Map());
      const entries = store.get(name);
      return {
        addAll: async (urls) => { for (const url of urls) entries.set(url, makeResponse(`precache ${url}`)); },
        put: async (request, response) => { entries.set(new URL(request.url).pathname, response); },
        keys: async () => [...entries.keys()],
      };
    },
    keys: async () => [...store.keys()],
    delete: async (name) => store.delete(name),
    match: async (request, options = {}) => {
      const url = typeof request === 'string' ? request : request.url;
      const path = url.startsWith('http') ? new URL(url).pathname : url;
      for (const entries of store.values()) {
        if (entries.has(path)) return entries.get(path);
      }
      return undefined;
    },
  };
  let claimed = false, skipped = false;
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type, handler) => { listeners[type] = handler; },
    skipWaiting: async () => { skipped = true; },
    clients: { claim: async () => { claimed = true; } },
  };
  const fakeFetch = async (request) => {
    fetched.push(new URL(request.url).pathname);
    const reply = network[new URL(request.url).pathname];
    if (reply === 'offline' || reply === undefined) throw new TypeError('Failed to fetch');
    return makeResponse(reply.body ?? 'réseau', reply.ok ?? true);
  };
  vm.runInNewContext(SOURCE.replace("'__BUILD__'", `'${build}'`), { self, caches: cacheApi, fetch: fakeFetch, URL, Promise, console });
  const dispatch = async (type, extra = {}) => {
    let pending;
    let response;
    const event = { waitUntil: (p) => { pending = p; }, respondWith: (p) => { response = p; }, ...extra };
    listeners[type](event);
    if (pending) await pending;
    return { responded: response !== undefined, response: response ? await response : undefined };
  };
  const request = (path, { method = 'GET', mode = 'cors', origin = ORIGIN } = {}) => ({ url: origin + path, method, mode });
  return { store, fetched, dispatch, request, flags: () => ({ claimed, skipped }), makeResponse };
}

test('installation : la coquille est mise en cache sous le nom de la version, et le nouveau service worker prend la main tout de suite', async () => {
  const sw = load({ build: 'v42' });
  await sw.dispatch('install');
  assert.deepEqual([...sw.store.keys()], ['wb-web-v42']);
  const cached = [...sw.store.get('wb-web-v42').keys()];
  for (const needed of ['/app/', '/app/app.js', '/app/app.css', '/app/logic.js', '/app/i18n.js', '/app/manifest.webmanifest']) assert.ok(cached.includes(needed), needed);
  assert.equal(sw.flags().skipped, true);
});

test('activation : les anciennes versions du cache disparaissent, les caches d\'autres origines non', async () => {
  const sw = load({ build: 'new', existingCaches: ['wb-web-old', 'wb-web-new', 'autre-application'] });
  await sw.dispatch('activate');
  assert.deepEqual([...sw.store.keys()].sort(), ['autre-application', 'wb-web-new']);
  assert.equal(sw.flags().claimed, true);
});

test('l\'API n\'est jamais interceptée : sons, lecture, session, activité, configuration', async () => {
  const sw = load();
  for (const path of ['/sounds', '/sounds/abc/file', '/play', '/stop', '/oauth/exchange', '/oauth/logout', '/activity', '/status?strict=1', '/my-guilds', '/shared-categories?guild_id=1', '/app/api/config']) {
    const { responded } = await sw.dispatch('fetch', { request: sw.request(path) });
    assert.equal(responded, false, `${path} ne doit pas passer par le service worker`);
  }
  assert.deepEqual(sw.fetched, []);
});

test('seules les lectures (GET) de la même origine sous /app/ sont gérées', async () => {
  const sw = load({ network: { '/app/app.js': { body: 'js' } } });
  assert.equal((await sw.dispatch('fetch', { request: sw.request('/app/app.js', { method: 'POST' }) })).responded, false);
  assert.equal((await sw.dispatch('fetch', { request: sw.request('/app/app.js', { origin: 'https://evil.example' }) })).responded, false);
  assert.equal((await sw.dispatch('fetch', { request: sw.request('/app') })).responded, false, '/app (sans « / ») n\'est pas dans le périmètre');
  assert.equal((await sw.dispatch('fetch', { request: sw.request('/app/app.js') })).responded, true);
});

test('réseau d\'abord : la page est toujours à jour, et la réponse est gardée en secours', async () => {
  const sw = load({ build: 'b', network: { '/app/app.js': { body: 'version fraîche' } } });
  await sw.dispatch('install');
  const { response } = await sw.dispatch('fetch', { request: sw.request('/app/app.js') });
  assert.equal(response.body, 'version fraîche');
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(sw.store.get('wb-web-b').get('/app/app.js').body, 'version fraîche', 'copie mise à jour dans le cache');
});

test('hors connexion : le cache prend le relais, la navigation retombe sur la page', async () => {
  const sw = load({ build: 'b', network: {} });
  await sw.dispatch('install');
  const script = await sw.dispatch('fetch', { request: sw.request('/app/app.js') });
  assert.equal(script.response.body, 'precache /app/app.js');
  const callback = await sw.dispatch('fetch', { request: sw.request('/app/callback?code=x', { mode: 'navigate' }) });
  assert.equal(callback.response.body, 'precache /app/', 'navigation inconnue : la coquille');
  await assert.rejects(sw.dispatch('fetch', { request: sw.request('/app/inconnu.png') }), TypeError, 'ressource absente du cache : l\'erreur réseau passe');
});

test('une réponse en erreur du serveur n\'écrase pas le cache', async () => {
  const sw = load({ build: 'b', network: { '/app/app.js': { body: 'erreur', ok: false } } });
  await sw.dispatch('install');
  const { response } = await sw.dispatch('fetch', { request: sw.request('/app/app.js') });
  assert.equal(response.ok, false);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(sw.store.get('wb-web-b').get('/app/app.js').body, 'precache /app/app.js');
});
