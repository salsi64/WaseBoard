// Le magasin de préférences de la page (prefs.js) : enregistrement dans le navigateur, échanges avec le serveur, modifications faites pendant
// un échange, reprise après une coupure. Faux serveur et fausses minuteries : rien d'asynchrone au hasard. Lancer : node --test server/tests/web/
import test from 'node:test';
import assert from 'node:assert/strict';
import { createPrefsStore, RETRY_DELAY_MS, SAVE_DELAY_MS } from '../../web/prefs.js';
import { addCategory, emptyPrefs, prefsEqual, sanitizePrefs, setMembership, toggleFavorite } from '../../web/logic.js';

const memoryStorage = (initial = {}) => {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k), data };
};

/** Minuteries à la main : `fire()` déclenche la plus récente encore programmée. */
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    setTimer: (fn, ms) => { const id = next++; pending.set(id, { fn, ms }); return id; },
    clearTimer: (id) => pending.delete(id),
    get delays() { return [...pending.values()].map((t) => t.ms); },
    async fire() { const [id, { fn }] = [...pending.entries()].pop(); pending.delete(id); fn(); await Promise.resolve(); },
    get count() { return pending.size; },
  };
}

/** Faux serveur : répond avec ce qu'on lui a dit de répondre, et garde les requêtes reçues. */
function fakeApi(responder) {
  const calls = [];
  const api = async (path, options) => {
    calls.push({ path, options });
    return responder(calls.length, options);
  };
  api.calls = calls;
  return api;
}

const ok = (doc) => ({ ok: true, status: 200, data: { rev: 1, doc } });
const offline = { ok: false, status: 0, data: null, offline: true };
const wire = (prefs) => ({ favorites: prefs.favorites, colors: prefs.colors, categories: Object.fromEntries(prefs.categories.map((c) => [c.name, c.ids])), order: prefs.order });

test('première synchronisation : favoris et couleurs d\'avant (gardés seuls dans le navigateur) sont envoyés, sans état de départ', async () => {
  const storage = memoryStorage();
  let firsts = 0;
  const api = fakeApi((n, options) => ok(options.body.doc));
  const store = createPrefsStore({ api, storage, userId: '20', legacy: { favorites: ['a', 'b'], colors: { a: '#111111' } }, onFirstSync: () => { firsts++; } });
  assert.deepEqual(store.prefs.favorites, ['a', 'b']);
  assert.equal(store.pending, true, 'rien n\'a encore été confirmé par le serveur');
  const outcome = await store.sync();
  assert.equal(outcome.ok, true);
  assert.equal(api.calls[0].path, '/me/preferences/sync');
  assert.equal(api.calls[0].options.method, 'POST');
  assert.equal(api.calls[0].options.body.base, null);
  assert.deepEqual(api.calls[0].options.body.doc.favorites, ['a', 'b']);
  assert.deepEqual(api.calls[0].options.body.doc.colors, { a: '#111111' });
  assert.equal(store.pending, false);
  assert.equal(firsts, 1);
  await store.sync();
  assert.equal(firsts, 1, 'une seule fois');
  assert.deepEqual(api.calls[1].options.body.base.favorites, ['a', 'b'], 'ensuite : l\'état reçu la fois d\'avant');
});

test('ce que le serveur ajoute (venu de l\'application Windows) est adopté et la page est prévenue', async () => {
  let changes = 0;
  const api = fakeApi(() => ok({ favorites: ['a', 'z'], colors: {}, categories: { Mèmes: ['a'] }, order: ['Mèmes'] }));
  const store = createPrefsStore({ api, storage: memoryStorage(), userId: '20', onChange: () => { changes++; } });
  await store.sync();
  assert.deepEqual(store.prefs.favorites, ['a', 'z']);
  assert.deepEqual(store.prefs.categories, [{ name: 'Mèmes', ids: ['a'] }]);
  assert.equal(changes, 1);
  await store.sync();
  assert.equal(changes, 1, 'rien de nouveau : la page n\'est pas redessinée');
});

test('une modification est visible tout de suite, gardée dans le navigateur, et envoyée après un court délai', async () => {
  const storage = memoryStorage();
  const timers = fakeTimers();
  let changes = 0;
  const api = fakeApi((n, options) => ok(options.body.doc));
  const store = createPrefsStore({ api, storage, userId: '20', onChange: () => { changes++; }, ...timers });
  store.edit((prefs) => toggleFavorite(prefs, 'a'));
  assert.deepEqual(store.prefs.favorites, ['a']);
  assert.equal(changes, 1);
  assert.deepEqual(JSON.parse(storage.getItem('wb.prefs.20')).local.favorites, ['a']);
  assert.deepEqual(timers.delays, [SAVE_DELAY_MS]);
  assert.equal(api.calls.length, 0, 'pas encore envoyé');
  store.edit((prefs) => toggleFavorite(prefs, 'b'));
  assert.equal(timers.count, 1, 'la série de modifications ne programme qu\'un envoi');
  await timers.fire();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(api.calls.length, 1);
  assert.deepEqual(api.calls[0].options.body.doc.favorites, ['a', 'b']);
  assert.equal(store.pending, false);
});

test('une modification qui ne change rien ne fait rien (même objet)', () => {
  const timers = fakeTimers();
  let changes = 0;
  const store = createPrefsStore({ api: fakeApi(() => offline), storage: memoryStorage(), userId: '20', onChange: () => { changes++; }, ...timers });
  store.edit((prefs) => prefs);
  store.edit((prefs) => setMembership(prefs, 'Inconnue', 'a', true));
  assert.equal(changes, 0);
  assert.equal(timers.count, 0);
});

test('modifié pendant un échange : le résultat n\'est pas adopté, l\'état de départ ne bouge pas, un nouvel échange suit', async () => {
  const timers = fakeTimers();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const bodies = [];
  const api = async (path, options) => {
    bodies.push(JSON.parse(JSON.stringify(options.body)));
    if (bodies.length === 1) { await gate; return ok({ favorites: ['a', 'remote'], colors: {}, categories: {}, order: [] }); }
    return ok(options.body.doc);
  };
  const store = createPrefsStore({ api, storage: memoryStorage(), userId: '20', ...timers });
  store.edit((prefs) => toggleFavorite(prefs, 'a'));
  const first = store.sync();
  store.edit((prefs) => toggleFavorite(prefs, 'b'));      // pendant l'échange
  release();
  await first;
  assert.deepEqual(store.prefs.favorites, ['a', 'b'], 'ma modification n\'est pas écrasée');
  assert.equal(store.pending, true);
  assert.ok(timers.count >= 1, 'un nouvel échange est programmé');
  await timers.fire();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(bodies[1].base, null, 'la base n\'a pas avancé : le serveur fusionne avec l\'ancien état de départ');
  assert.deepEqual(bodies[1].doc.favorites, ['a', 'b']);
});

test('hors connexion : les modifications restent, un nouvel essai est programmé, puis tout part au retour du réseau', async () => {
  const timers = fakeTimers();
  let online = false;
  const api = fakeApi((n, options) => (online ? ok(options.body.doc) : offline));
  const store = createPrefsStore({ api, storage: memoryStorage(), userId: '20', ...timers });
  store.edit((prefs) => addCategory(prefs, 'Mèmes'));
  const outcome = await store.sync();
  assert.deepEqual({ ok: outcome.ok, offline: outcome.offline }, { ok: false, offline: true });
  assert.equal(store.pending, true);
  assert.ok(timers.delays.includes(RETRY_DELAY_MS), 'nouvel essai programmé');
  online = true;
  await store.sync();
  assert.equal(store.pending, false);
  assert.deepEqual(store.prefs.categories.map((c) => c.name), ['Mèmes']);
});

test('réponse du serveur inattendue ou en erreur : rien n\'est adopté', async () => {
  for (const reply of [{ ok: false, status: 500, data: { error: 'boum' } }, { ok: true, status: 200, data: null }, { ok: true, status: 200, data: { doc: 'texte' } }]) {
    const store = createPrefsStore({ api: fakeApi(() => reply), storage: memoryStorage(), userId: '20', ...fakeTimers() });
    store.edit((prefs) => toggleFavorite(prefs, 'a'));
    const outcome = await store.sync();
    assert.equal(outcome.ok, false);
    assert.deepEqual(store.prefs.favorites, ['a']);
    assert.equal(store.pending, true);
  }
});

test('un autre onglet ou une autre visite retrouve l\'état gardé dans le navigateur, chaque personne a le sien', async () => {
  const storage = memoryStorage();
  const api = fakeApi((n, options) => ok(options.body.doc));
  const first = createPrefsStore({ api, storage, userId: '20', ...fakeTimers() });
  first.edit((prefs) => toggleFavorite(prefs, 'a'));
  await first.sync();
  first.edit((prefs) => toggleFavorite(prefs, 'b'));      // pas encore envoyé
  const again = createPrefsStore({ api, storage, userId: '20', ...fakeTimers() });
  assert.deepEqual(again.prefs.favorites, ['a', 'b']);
  assert.equal(again.pending, true, 'la modification non envoyée l\'est encore à la prochaine synchronisation');
  await again.sync();
  assert.deepEqual(api.calls.at(-1).options.body.base.favorites, ['a'], 'avec la base de la dernière fois');
  assert.deepEqual(api.calls.at(-1).options.body.doc.favorites, ['a', 'b']);
  const other = createPrefsStore({ api, storage, userId: '21', ...fakeTimers() });
  assert.deepEqual(other.prefs, emptyPrefs());
});

test('après stop(), plus rien n\'est envoyé', async () => {
  const timers = fakeTimers();
  const api = fakeApi((n, options) => ok(options.body.doc));
  const store = createPrefsStore({ api, storage: memoryStorage(), userId: '20', ...timers });
  store.edit((prefs) => toggleFavorite(prefs, 'a'));
  store.stop();
  assert.equal(timers.count, 0);
  assert.equal((await store.sync()).ok, false);
  assert.equal(api.calls.length, 0);
});

test('un état enregistré abîmé est ignoré sans exception', () => {
  const storage = memoryStorage({ 'wb.prefs.20': '{pas du json' });
  const store = createPrefsStore({ api: fakeApi(() => offline), storage, userId: '20', ...fakeTimers() });
  assert.ok(prefsEqual(store.prefs, sanitizePrefs(null)));
});
