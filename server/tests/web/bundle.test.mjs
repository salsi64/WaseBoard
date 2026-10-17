// Cohérence de l'ensemble de la page web (server/web/) : traductions, service worker, manifeste, et règles de sécurité
// que la politique de contenu impose. Lancer : node --test server/tests/web/
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { MESSAGES, LANGUAGES, makeTranslator, pickLanguage } from '../../web/i18n.js';

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'web');
const read = (name) => readFileSync(join(WEB, name), 'utf8');
const walk = (dir) => readdirSync(dir).flatMap((f) => (statSync(join(dir, f)).isDirectory() ? walk(join(dir, f)) : [join(dir, f)]));
const files = walk(WEB).map((f) => relative(WEB, f).split('\\').join('/'));

test('traductions : mêmes clés et mêmes {n} dans toutes les langues', () => {
  const base = Object.keys(MESSAGES.fr).sort();
  for (const lang of LANGUAGES) {
    assert.deepEqual(Object.keys(MESSAGES[lang]).sort(), base, `clés de « ${lang} » différentes du français`);
  }
  const placeholders = (text) => [...text.matchAll(/\{(\d+)\}/g)].map((m) => m[1]).sort().join(',');
  for (const key of base) {
    for (const lang of LANGUAGES) assert.equal(placeholders(MESSAGES[lang][key]), placeholders(MESSAGES.fr[key]), `${key} (${lang})`);
    assert.ok(MESSAGES.fr[key].length > 0 && MESSAGES.en[key].length > 0, `${key} vide`);
  }
});

test('traductions : chaque clé employée dans app.js existe', () => {
  const code = read('app.js');
  const viaTranslator = [...code.matchAll(/\bt\('([^']+)'/g)].map((m) => m[1]);
  // Clés passées par leur nom (endSession('login.expired')…) : toujours de la forme « domaine.nom ».
  const named = [...code.matchAll(/'((?:login|voice|menu|sheet|tabs|empty|search|section|sound|preview|load)\.[A-Za-z.]+)'/g)].map((m) => m[1]);
  assert.ok(viaTranslator.length > 15, 'des clés sont employées');
  for (const key of new Set([...viaTranslator, ...named])) assert.ok(key in MESSAGES.fr, `clé inconnue : ${key}`);
});

test('traducteur : paramètres, repli sur le français puis sur la clé', () => {
  const fr = makeTranslator('fr');
  assert.equal(fr('voice.joined', 'Salon'), 'Le bot a rejoint « Salon ».');
  assert.equal(makeTranslator('en')('voice.joined', 'Room'), 'The bot joined “Room”.');
  assert.equal(makeTranslator('de')('stop'), 'Stop', 'langue inconnue : français');
  assert.equal(fr('clé.inconnue'), 'clé.inconnue');
  assert.equal(fr('voice.joined'), 'Le bot a rejoint « {0} ».', 'paramètre manquant laissé visible');
  assert.equal(pickLanguage(['de-DE', 'en-GB', 'fr']), 'en');
  assert.equal(pickLanguage(['fr-CA']), 'fr');
  assert.equal(pickLanguage(['ja']), 'fr');
  assert.equal(pickLanguage(undefined), 'fr');
});

test('service worker : chaque fichier de la coquille existe, et le jeton de version est présent', () => {
  const sw = read('sw.js');
  assert.match(sw, /const BUILD = '__BUILD__';/);
  const shell = [...sw.matchAll(/'(\/app\/[^']*)'/g)].map((m) => m[1]).filter((p) => !p.endsWith('/app/api/') && p !== '/app/');
  assert.ok(shell.length >= 6);
  for (const path of shell) assert.ok(existsSync(join(WEB, path.replace('/app/', ''))), `${path} est précaché mais absent`);
  for (const needed of ['app.js', 'app.css', 'logic.js', 'i18n.js', 'manifest.webmanifest']) assert.ok(shell.includes(`/app/${needed}`), `${needed} devrait être précaché`);
  assert.match(sw, /startsWith\('\/app\/api\/'\)/, 'la configuration ne doit pas être mise en cache');
  assert.ok(!/caches\.[a-z]+\([^)]*(sounds|oauth|activity|status|play|stop)/.test(sw), 'aucune route d\'API dans le cache');
});

test('manifeste : installable', () => {
  const manifest = JSON.parse(read('manifest.webmanifest'));
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.start_url, '/app/');
  assert.equal(manifest.scope, '/app/');
  assert.ok(manifest.name && manifest.short_name);
  const sizes = manifest.icons.map((i) => `${i.sizes}/${i.purpose}`);
  assert.ok(sizes.includes('192x192/any') && sizes.includes('512x512/any') && sizes.includes('512x512/maskable'), sizes.join());
  for (const icon of manifest.icons) assert.ok(existsSync(join(WEB, icon.src.replace('/app/', ''))), `${icon.src} absent`);
  assert.ok(manifest.start_url.startsWith(manifest.scope));
});

test('index.html : fichiers référencés présents, aucun script ni style en ligne (politique de sécurité de contenu)', () => {
  const html = read('index.html');
  for (const [, url] of html.matchAll(/(?:href|src)="(\/app\/[^"]+)"/g)) assert.ok(existsSync(join(WEB, url.replace('/app/', ''))), `${url} absent`);
  assert.ok(!/<script(?![^>]*\bsrc=)/i.test(html), 'script en ligne');
  assert.ok(!/<style/i.test(html), 'style en ligne');
  assert.ok(!/\son[a-z]+="/i.test(html), 'gestionnaire d\'événement en ligne');
  assert.ok(!/style="/i.test(html), 'attribut style');
  assert.match(html, /<script type="module" src="\/app\/app\.js"><\/script>/);
  assert.match(html, /<meta name="viewport"[^>]*viewport-fit=cover/);
  assert.match(html, /rel="manifest"/);
  assert.match(html, /name="wb-build" content="__BUILD__"/);
});

test('code : pas d\'innerHTML, d\'eval ni de document.write (les noms de sons sont du texte, jamais du HTML)', () => {
  for (const name of ['app.js', 'logic.js', 'i18n.js', 'sw.js']) {
    const code = read(name);
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\(/.test(code), `${name} : API dangereuse`);
    assert.ok(!/setAttribute\(\s*['"]style['"]/.test(code), `${name} : style posé par attribut (bloqué par la CSP)`);
  }
  assert.ok(!/console\.(log|info|debug)/.test(read('app.js')), 'pas de journal dans la console');
});

test('le secret partagé de l\'instance n\'apparaît nulle part dans la page', () => {
  for (const name of files.filter((f) => /\.(js|html|css|webmanifest)$/.test(f))) {
    const text = read(name);
    assert.ok(!/X-WaseBoard-Token/i.test(text), `${name} : en-tête du secret partagé`);
    assert.ok(!/shared_secret|bot_token|client_secret/i.test(text), `${name} : mot-clé de secret`);
  }
});

test('l\'API appelée par la page existe côté serveur', () => {
  const server = readFileSync(join(WEB, '..', 'server.py'), 'utf8');
  const calls = [...read('app.js').matchAll(/api\(`?'?(\/[a-z-]+)/g)].map((m) => m[1]);
  assert.ok(calls.length >= 6);
  for (const route of new Set(calls)) assert.ok(server.includes(`"${route}`), `route ${route} absente de server.py`);
});

test('icônes : fichiers PNG valides aux bonnes tailles', () => {
  const sizeOf = (name) => {
    const buffer = readFileSync(join(WEB, 'icons', name));
    assert.equal(buffer.subarray(1, 4).toString(), 'PNG', name);
    return [buffer.readUInt32BE(16), buffer.readUInt32BE(20)];
  };
  assert.deepEqual(sizeOf('icon-192.png'), [192, 192]);
  assert.deepEqual(sizeOf('icon-512.png'), [512, 512]);
  assert.deepEqual(sizeOf('icon-maskable-512.png'), [512, 512]);
  assert.deepEqual(sizeOf('apple-touch-icon.png'), [180, 180]);
});
