// Logique pure de la page web (server/web/logic.js). Lancer : node --test server/tests/web/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PALETTE, addCategory, autoColor, buildAuthorizeUrl, buildSections, categoryPosition, categoryTabs, checkCategoryName, colorFor, dedupeById, emptyPrefs, errorText,
  favoriteSounds, filterSounds, isHexColor, loadJson, makeState, moveCategory, normalizeText, orderedEntries, parseCallback, personalKey, playingMap, prefsEqual,
  prefsToWire, removeCategory, renameCategory, sanitizeColors, sanitizeIds, sanitizePrefs, sanitizeSession, saveJson, setColor, setMembership, sortByName,
  toggleFavorite, toggleInSet, trimRange, voiceSummary,
} from '../../web/logic.js';

const memoryStorage = (initial = {}) => {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
};
const sound = (id, name, extra = {}) => ({ id, name, ...extra });

test('couleur automatique : mêmes couleurs que l\'application Windows (hash × 31 + caractère sur 32 bits)', () => {
  // Valeurs calculées à la main avec l'algorithme de IdToColorConverter.AutoColorFor.
  const csharp = (id) => { let h = 0; for (const c of id) h = (Math.imul(h, 31) + c.charCodeAt(0)) | 0; return PALETTE[Math.abs(h) % PALETTE.length]; };
  for (const id of ['a', 'b', 'abc', 'f3a9c1d2e4b5', 'Éléphant', 'z'.repeat(80), '']) assert.equal(autoColor(id), csharp(id), id);
  assert.equal(autoColor('abc'), PALETTE[(('a'.charCodeAt(0) * 31 + 'b'.charCodeAt(0)) * 31 + 'c'.charCodeAt(0)) % 10]);
  assert.equal(autoColor('x1'), autoColor('x1'), 'stable');
  assert.ok(PALETTE.includes(autoColor('n\'importe quoi')));
});

test('une longue chaîne ne dépasse pas la capacité d\'un entier 32 bits (le hash reste borné)', () => {
  const color = autoColor('é'.repeat(5000));
  assert.ok(PALETTE.includes(color));
});

test('couleur effective : celle choisie si valide, sinon l\'automatique', () => {
  const s = sound('abc', 'x');
  assert.equal(colorFor(s, {}), autoColor('abc'));
  assert.equal(colorFor(s, { abc: '#112233' }), '#112233');
  assert.equal(colorFor(s, { abc: 'rouge' }), autoColor('abc'));
  assert.equal(colorFor(s, undefined), autoColor('abc'));
  assert.ok(isHexColor('#AaBb09') && !isHexColor('#abc') && !isHexColor('#12345g') && !isHexColor(null) && !isHexColor('red'));
});

test('recherche : sans accents ni casse, tous les mots doivent apparaître', () => {
  const sounds = [sound('1', 'Éléphant qui barrit'), sound('2', 'Applaudissements'), sound('3', 'Elephant rose'), sound('4', 'ÇA ROULE')];
  assert.deepEqual(filterSounds(sounds, 'elephant').map((s) => s.id), ['1', '3']);
  assert.deepEqual(filterSounds(sounds, 'ÉLÉPHANT barrit').map((s) => s.id), ['1']);
  assert.deepEqual(filterSounds(sounds, 'ca roule').map((s) => s.id), ['4']);
  assert.deepEqual(filterSounds(sounds, '  ').map((s) => s.id), ['1', '2', '3', '4']);
  assert.deepEqual(filterSounds(sounds, 'zzz'), []);
  assert.equal(normalizeText(null), '');
  assert.equal(normalizeText('Œuf'), 'œuf'.normalize('NFD').replace(/[̀-ͯ]/g, ''));
});

test('tri par nom : insensible à la casse et aux accents, nombres dans l\'ordre naturel', () => {
  const names = sortByName([sound('a', 'zèbre'), sound('b', 'Abeille'), sound('c', 'son 10'), sound('d', 'son 2'), sound('e', 'école')]).map((s) => s.name);
  assert.deepEqual(names, ['Abeille', 'école', 'son 2', 'son 10', 'zèbre']);
  const input = [sound('a', 'b'), sound('b', 'a')];
  sortByName(input);
  assert.equal(input[0].id, 'a', 'ne modifie pas la liste d\'origine');
});

test('sections : un serveur = ses sons + ceux partagés chez lui ; un son partagé apparaît dans chaque serveur', () => {
  const guilds = [{ id: 'G1', name: 'Alpha' }, { id: 'G2', name: 'Beta' }, { id: 'G3', name: 'Vide' }];
  const sounds = [sound('a', 'Alpha 1', { guild_id: 'G1' }), sound('b', 'Beta 1', { guild_id: 'G2' }), sound('c', 'Alpha 0', { guild_id: 'G1' }), sound('d', 'Orphelin', { guild_id: 'G9' })];
  const sections = buildSections(sounds, guilds, { G1: ['b'], G3: [] });
  assert.deepEqual(sections.map((s) => s.key), ['G1', 'G2', 'other'], 'serveur sans son : pas de section ; orphelins en dernier');
  assert.deepEqual(sections[0].sounds.map((s) => s.id), ['c', 'a', 'b'], 'natifs + partagé, triés par nom');
  assert.deepEqual(sections[1].sounds.map((s) => s.id), ['b']);
  assert.deepEqual(sections[2].sounds.map((s) => s.id), ['d']);
  assert.equal(sections[2].guild, null);
  assert.deepEqual(buildSections([], guilds, {}), []);
  assert.deepEqual(buildSections(sounds, [], null).map((s) => s.key), ['other'], 'sans serveur connu : tout dans « autres »');
});

test('préférences reçues du serveur : nettoyées, jamais d\'exception, « __proto__ » reste un simple nom', () => {
  assert.deepEqual(sanitizePrefs(null), emptyPrefs());
  assert.deepEqual(sanitizePrefs('texte'), emptyPrefs());
  assert.deepEqual(sanitizePrefs({ categories: [], order: 'x', favorites: {}, colors: [] }), emptyPrefs());
  const clean = sanitizePrefs({
    favorites: ['a', 'a', 3, '', 'b'], colors: { a: '#aabbcc', b: 'rouge', c: 7 },
    categories: { Mèmes: ['a', 3, '', 'b'], Vide: [], Faux: 'abc', ['x'.repeat(101)]: ['a'], '  Jeux ': ['z'] }, order: ['Mèmes', 7, 'G1', 'G1'],
  });
  assert.deepEqual(clean, {
    favorites: ['a', 'b'], colors: { a: '#AABBCC' },
    categories: [{ name: 'Mèmes', ids: ['a', 'b'] }, { name: 'Vide', ids: [] }, { name: 'Jeux', ids: ['z'] }], order: ['Mèmes', 'G1'],
  });
  const proto = sanitizePrefs(JSON.parse('{"categories":{"__proto__":["a"]}}'));
  assert.deepEqual(proto.categories, [{ name: '__proto__', ids: ['a'] }]);
  assert.equal(Object.getPrototypeOf(proto.categories[0].ids), Array.prototype, 'aucun objet n\'est détourné');
});

const personalFixture = () => ({
  guilds: [{ id: 'G1', name: 'Alpha' }, { id: 'G2', name: 'Beta' }],
  sounds: [sound('a', 'Alpha 1', { guild_id: 'G1' }), sound('b', 'Beta 1', { guild_id: 'G2' }), sound('c', 'Alpha 0', { guild_id: 'G1' }), sound('d', 'Orphelin', { guild_id: 'G9' })],
  personal: sanitizePrefs({ categories: { Mèmes: ['b', 'a', 'd', 'supprimé'], Vide: ['inconnu'], Jeux: ['c'] }, order: ['G2', 'Mèmes', 'G1', 'Jeux', 'Vide'] }),
});

test('ordre : celui de l\'application Windows (serveurs et catégories mélangés), le reste ensuite', () => {
  const { guilds, personal } = personalFixture();
  const label = (entries) => entries.map((e) => (e.type === 'guild' ? e.guild.id : personalKey(e.name)));
  assert.deepEqual(label(orderedEntries(guilds, personal)), ['G2', 'cat:Mèmes', 'G1', 'cat:Jeux', 'cat:Vide']);
  assert.deepEqual(label(orderedEntries(guilds, sanitizePrefs({ categories: { Z: ['a'], A: ['b'] } }))), ['G1', 'G2', 'cat:Z', 'cat:A'], 'sans ordre : serveurs puis catégories');
  assert.deepEqual(label(orderedEntries(guilds, sanitizePrefs({ categories: { A: [] }, order: ['A', 'A', 'G9', 'G1', 'G1'] }))), ['cat:A', 'G1', 'G2'], 'doublons et inconnus ignorés');
  assert.deepEqual(label(orderedEntries(guilds, null)), ['G1', 'G2'], 'sans catégories : comme avant');
});

test('onglets : les serveurs et les catégories personnelles, même vides (on peut les remplir depuis la page)', () => {
  const { guilds, sounds, personal } = personalFixture();
  const tabs = categoryTabs(sounds, guilds, personal);
  assert.deepEqual(tabs.map((t) => t.key), ['G2', 'cat:Mèmes', 'G1', 'cat:Jeux', 'cat:Vide']);
  assert.equal(tabs[1].type, 'personal');
  assert.equal(tabs[1].name, 'Mèmes');
  assert.deepEqual(categoryTabs(sounds, guilds, null).map((t) => t.key), ['G1', 'G2'], 'sans catégories : comme avant');
  assert.deepEqual(categoryTabs([], guilds, personal).map((t) => t.key), ['G2', 'cat:Mèmes', 'G1', 'cat:Jeux', 'cat:Vide'], 'catalogue vide : les catégories restent');
});

test('sections : les catégories personnelles s\'intercalent selon l\'ordre, sans retirer les sons des serveurs', () => {
  const { guilds, sounds, personal } = personalFixture();
  const sections = buildSections(sounds, guilds, {}, personal);
  assert.deepEqual(sections.map((s) => s.key), ['G2', 'cat:Mèmes', 'G1', 'cat:Jeux', 'cat:Vide', 'other']);
  assert.deepEqual(sections.map((s) => s.kind), ['guild', 'personal', 'guild', 'personal', 'personal', 'other']);
  assert.deepEqual(sections[4].sounds, [], 'une catégorie sans son visible reste affichée (vide)');
  assert.deepEqual(sections[1].sounds.map((s) => s.id), ['a', 'b', 'd'], 'triés par nom ; le son supprimé a disparu');
  assert.equal(sections[1].name, 'Mèmes');
  assert.equal(sections[1].guild, null);
  assert.deepEqual(sections[2].sounds.map((s) => s.id), ['c', 'a'], 'le serveur garde tous ses sons');
  assert.deepEqual(sections[5].sounds.map((s) => s.id), ['d'], 'un son de catégorie perso mais d\'aucun serveur reste dans « autres »');
  assert.deepEqual(buildSections(sounds, guilds, {}, null).map((s) => s.key), ['G1', 'G2', 'other'], 'sans catégories : inchangé');
});

test('préférences : forme envoyée au serveur, et comparaison (ensembles pour les favoris et le contenu, suite pour l\'ordre)', () => {
  const prefs = sanitizePrefs({ favorites: ['a', 'b'], colors: { a: '#111111' }, categories: { X: ['1', '2'] }, order: ['X', 'G1'] });
  assert.deepEqual(prefsToWire(prefs), { favorites: ['a', 'b'], colors: { a: '#111111' }, categories: { X: ['1', '2'] }, order: ['X', 'G1'] });
  assert.deepEqual(prefsToWire(sanitizePrefs(JSON.parse('{"categories":{"__proto__":["a"]}}'))).categories, JSON.parse('{"__proto__":["a"]}'), '« __proto__ » reste une clé');
  assert.ok(prefsEqual(prefs, sanitizePrefs({ favorites: ['b', 'a'], colors: { a: '#111111' }, categories: { X: ['2', '1'] }, order: ['X', 'G1'] })));
  assert.ok(!prefsEqual(prefs, sanitizePrefs({ favorites: ['b', 'a'], colors: { a: '#111111' }, categories: { X: ['2', '1'] }, order: ['G1', 'X'] })), 'l\'ordre compte');
  assert.ok(!prefsEqual(prefs, sanitizePrefs({ favorites: ['a'], colors: { a: '#111111' }, categories: { X: ['1', '2'] }, order: ['X', 'G1'] })));
  assert.ok(!prefsEqual(prefs, sanitizePrefs({ favorites: ['a', 'b'], colors: { a: '#222222' }, categories: { X: ['1', '2'] }, order: ['X', 'G1'] })));
  assert.ok(!prefsEqual(prefs, sanitizePrefs({ favorites: ['a', 'b'], colors: { a: '#111111' }, categories: { Y: ['1', '2'] }, order: ['X', 'G1'] })));
  assert.ok(prefsEqual(emptyPrefs(), sanitizePrefs(undefined)));
});

test('préférences : favoris et couleurs, sans modifier l\'original', () => {
  const start = emptyPrefs();
  const fav = toggleFavorite(start, 'a');
  assert.deepEqual(fav.favorites, ['a']);
  assert.deepEqual(toggleFavorite(fav, 'a').favorites, []);
  assert.deepEqual(start.favorites, [], 'l\'original est intact');
  const colored = setColor(start, 'a', '#aabbcc');
  assert.deepEqual(colored.colors, { a: '#AABBCC' });
  assert.deepEqual(setColor(colored, 'a', null).colors, {}, 'null : couleur automatique');
  assert.deepEqual(setColor(colored, 'a', 'rouge').colors, {}, 'une couleur invalide revient à l\'automatique');
});

test('catégories : créer, renommer, supprimer, remplir', () => {
  let prefs = addCategory(emptyPrefs(), 'Mèmes');
  assert.deepEqual(prefs.categories, [{ name: 'Mèmes', ids: [] }]);
  assert.deepEqual(prefs.order, ['Mèmes'], 'la nouvelle catégorie va en fin d\'ordre');
  assert.equal(addCategory(prefs, 'Mèmes'), prefs, 'déjà là : rien ne change');
  prefs = setMembership(setMembership(prefs, 'Mèmes', 'a', true), 'Mèmes', 'b', true);
  assert.deepEqual(prefs.categories[0].ids, ['a', 'b']);
  assert.equal(setMembership(prefs, 'Mèmes', 'a', true), prefs, 'déjà dedans : rien ne change');
  assert.deepEqual(setMembership(prefs, 'Mèmes', 'a', false).categories[0].ids, ['b']);
  assert.deepEqual(setMembership(prefs, 'Inconnue', 'a', true), prefs, 'catégorie inconnue : rien ne change');
  prefs = { ...addCategory(prefs, 'Jeux'), order: ['Mèmes', '3101', 'Jeux'] };
  const renamed = renameCategory(prefs, 'Mèmes', 'Drôle');
  assert.deepEqual(renamed.categories.map((c) => [c.name, c.ids]), [['Drôle', ['a', 'b']], ['Jeux', []]], 'contenu et place gardés');
  assert.deepEqual(renamed.order, ['Drôle', '3101', 'Jeux']);
  assert.equal(renameCategory(prefs, 'Mèmes', 'Jeux'), prefs, 'le nom existe déjà : refusé');
  const removed = removeCategory(prefs, 'Mèmes');
  assert.deepEqual(removed.categories.map((c) => c.name), ['Jeux']);
  assert.deepEqual(removed.order, ['3101', 'Jeux']);
});

test('catégories : nom valide (rogné, non vide, 100 caractères au plus, pas déjà pris)', () => {
  const prefs = addCategory(emptyPrefs(), 'Mèmes');
  assert.deepEqual(checkCategoryName(prefs, '  Jeux  '), { name: 'Jeux' });
  assert.deepEqual(checkCategoryName(prefs, '   '), { error: 'empty' });
  assert.deepEqual(checkCategoryName(prefs, undefined), { error: 'empty' });
  assert.deepEqual(checkCategoryName(prefs, 'x'.repeat(101)), { error: 'long' });
  assert.deepEqual(checkCategoryName(prefs, 'x'.repeat(100)), { name: 'x'.repeat(100) });
  assert.deepEqual(checkCategoryName(prefs, '😀'.repeat(100)), { name: '😀'.repeat(100) }, 'on compte des caractères, pas des unités UTF-16');
  assert.deepEqual(checkCategoryName(prefs, 'Mèmes'), { error: 'exists' });
  assert.deepEqual(checkCategoryName(prefs, 'Mèmes', 'Mèmes'), { name: 'Mèmes' }, 'garder son propre nom en renommant n\'est pas un doublon');
});

test('catégories : déplacer d\'un cran parmi les catégories ET les serveurs, comme dans l\'application', () => {
  const guilds = [{ id: 'G1', name: 'Alpha' }, { id: 'G2', name: 'Beta' }];
  const prefs = sanitizePrefs({ categories: { A: [], B: [] }, order: ['G1', 'A', 'G2', 'B'] });
  assert.deepEqual(moveCategory(prefs, guilds, 'A', -1).order, ['A', 'G1', 'G2', 'B']);
  assert.deepEqual(moveCategory(prefs, guilds, 'A', 1).order, ['G1', 'G2', 'A', 'B']);
  assert.equal(moveCategory(prefs, guilds, 'B', 1), prefs, 'déjà en dernier');
  assert.equal(moveCategory(sanitizePrefs({ categories: { A: [] }, order: ['A', 'G1', 'G2'] }), guilds, 'A', -1).order.join(), 'A,G1,G2', 'déjà en premier');
  assert.deepEqual(categoryPosition(prefs, guilds, 'A'), { first: false, last: false });
  assert.deepEqual(categoryPosition(prefs, guilds, 'B'), { first: false, last: true });
  assert.deepEqual(categoryPosition(sanitizePrefs({ categories: { A: [] }, order: ['A'] }), [], 'A'), { first: true, last: true });
  const unordered = sanitizePrefs({ categories: { A: [], B: [] } });
  assert.deepEqual(moveCategory(unordered, guilds, 'B', -1).order, ['G1', 'G2', 'B', 'A'], 'sans ordre enregistré : l\'ordre affiché est complété puis modifié');
});

test('favoris : seulement les sons existants, triés, sans doublon', () => {
  const sounds = [sound('a', 'Zèbre'), sound('b', 'Abeille'), sound('a', 'Zèbre'), sound('c', 'Chat')];
  assert.deepEqual(favoriteSounds(sounds, new Set(['a', 'b', 'supprimé'])).map((s) => s.id), ['b', 'a']);
  assert.deepEqual(favoriteSounds(sounds, new Set()), []);
  assert.deepEqual(dedupeById(sounds).map((s) => s.id), ['a', 'b', 'c']);
});

test('basculer un favori ne modifie pas l\'ensemble d\'origine', () => {
  const start = new Set(['a']);
  const added = toggleInSet(start, 'b');
  const removed = toggleInSet(added, 'a');
  assert.deepEqual([...added].sort(), ['a', 'b']);
  assert.deepEqual([...removed], ['b']);
  assert.deepEqual([...start], ['a']);
});

test('stockage : jamais d\'exception, valeurs illisibles ignorées', () => {
  const storage = memoryStorage({ ok: '{"a":1}', broken: '{pas du json' });
  assert.deepEqual(loadJson(storage, 'ok', null), { a: 1 });
  assert.equal(loadJson(storage, 'broken', 'repli'), 'repli');
  assert.equal(loadJson(storage, 'absent', 'repli'), 'repli');
  assert.equal(saveJson(storage, 'k', [1, 2]), true);
  assert.deepEqual(loadJson(storage, 'k', null), [1, 2]);
  const refusing = { getItem() { throw new Error('refusé'); }, setItem() { throw new Error('quota'); } };
  assert.equal(loadJson(refusing, 'k', 'repli'), 'repli');
  assert.equal(saveJson(refusing, 'k', 1), false);
});

test('ce qui vient du stockage est assaini', () => {
  assert.deepEqual([...sanitizeIds(['a', 3, null, '', 'b', 'x'.repeat(300)])], ['a', 'b']);
  assert.deepEqual([...sanitizeIds('pas une liste')], []);
  assert.deepEqual(sanitizeColors({ a: '#112233', b: 'red', c: 5, d: '#12345' }), { a: '#112233' });
  assert.deepEqual(sanitizeColors([1, 2]), {});
  assert.deepEqual(sanitizeColors(null), {});
  assert.equal(sanitizeSession(null), null);
  assert.equal(sanitizeSession({ session_token: 'court' }), null);
  assert.equal(sanitizeSession({ session_token: 'x'.repeat(20), avatar_url: 'https://evil.example/a.png' }).avatar_url, null, 'avatar : seulement le CDN de Discord');
  const ok = sanitizeSession({ session_token: 'x'.repeat(20), user_id: 12, username: 'Alice', avatar_url: 'https://cdn.discordapp.com/avatars/1/a.png', extra: 'ignoré' });
  assert.deepEqual(ok, { session_token: 'x'.repeat(20), user_id: '12', username: 'Alice', avatar_url: 'https://cdn.discordapp.com/avatars/1/a.png' });
});

test('connexion Discord : état aléatoire, adresse d\'autorisation, lecture du retour', () => {
  const fixed = (n) => Uint8Array.from({ length: n }, (_, i) => i * 17 % 256);
  const state = makeState(fixed);
  assert.match(state, /^[0-9a-f]{32}$/);
  assert.notEqual(makeState((n) => crypto.getRandomValues(new Uint8Array(n))), makeState((n) => crypto.getRandomValues(new Uint8Array(n))));

  const url = new URL(buildAuthorizeUrl({ client_id: '123', redirect_uri: 'https://x.org/app/callback', scope: 'identify' }, state));
  assert.equal(url.origin + url.pathname, 'https://discord.com/oauth2/authorize');
  assert.equal(url.searchParams.get('client_id'), '123');
  assert.equal(url.searchParams.get('redirect_uri'), 'https://x.org/app/callback');
  assert.equal(url.searchParams.get('response_type'), 'code');
  assert.equal(url.searchParams.get('scope'), 'identify');
  assert.equal(url.searchParams.get('state'), state);
  assert.equal(new URL(buildAuthorizeUrl({ client_id: '1', redirect_uri: 'https://x/c' }, 's')).searchParams.get('scope'), 'identify');

  assert.deepEqual(parseCallback('?code=abc&state=xyz'), { code: 'abc', state: 'xyz', error: null });
  assert.deepEqual(parseCallback('?error=access_denied&state=xyz'), { code: null, state: 'xyz', error: 'access_denied' });
  assert.deepEqual(parseCallback(''), { code: null, state: null, error: null });
});

test('réponses du serveur', () => {
  assert.equal(errorText(409, { error: 'Le bot n\'est connecté à aucun salon vocal.' }), 'Le bot n\'est connecté à aucun salon vocal.');
  assert.equal(errorText(500, null), 'HTTP 500');
  assert.equal(errorText(500, { error: '' }), 'HTTP 500');
  assert.equal(errorText(0, null), 'réseau');

  assert.deepEqual(trimRange({ trim_start_ms: 500, trim_end_ms: 2500 }), { start: 0.5, end: 2.5 });
  assert.equal(trimRange({}), null);
  assert.equal(trimRange({ trim_start_ms: 3000, trim_end_ms: 1000 }), null);
  assert.equal(trimRange({ trim_start_ms: 0, trim_end_ms: null }), null);

  const map = playingMap({ activity: { a: [{ user_id: '1' }], b: [], c: 'x' }, online: [] });
  assert.deepEqual([...map.keys()], ['a']);
  assert.equal(playingMap(null).size, 0);
  assert.equal(playingMap({}).size, 0);

  assert.deepEqual(voiceSummary({ connected: true, channel: 'général', guild_name: 'Alpha', guild_id: '1' }), { connected: true, channel: 'général', guildName: 'Alpha', guildId: '1' });
  assert.equal(voiceSummary({ connected: false }).connected, false);
  assert.equal(voiceSummary(null).connected, false);
});

test('Activité Discord : reconnaissance du contexte et repli du CDN', async () => {
  const { isActivityLocation, cdnFallbackUrl } = await import('../../web/logic.js');
  assert.equal(isActivityLocation('?frame_id=123&instance_id=abc&platform=desktop'), true);
  assert.equal(isActivityLocation('?instance_id=abc'), false);
  assert.equal(isActivityLocation(''), false);
  assert.equal(isActivityLocation('?code=x&state=y'), false, 'retour de connexion web : pas une Activité');
  assert.equal(cdnFallbackUrl('https://cdn.discordapp.com/avatars/1/abc.png?size=64'), '/discord-cdn/avatars/1/abc.png?size=64');
  assert.equal(cdnFallbackUrl('https://cdn.discordapp.com/icons/9/i.webp'), '/discord-cdn/icons/9/i.webp');
  assert.equal(cdnFallbackUrl('https://evil.example/avatars/1.png'), null, 'seulement le CDN de Discord');
  assert.equal(cdnFallbackUrl('https://cdn.discordapp.com.evil.example/x.png'), null);
  assert.equal(cdnFallbackUrl(null), null);
  assert.equal(cdnFallbackUrl('/app/icons/icon-192.png'), null);
});
