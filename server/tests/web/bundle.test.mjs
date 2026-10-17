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
// Les scripts de la page (le service worker, particulier, est vérifié à part).
const SCRIPTS = files.filter((f) => f.endsWith('.js') && f !== 'sw.js' && !f.startsWith('vendor/'));
// Chargé seulement dans une Activité Discord (jamais par le navigateur ordinaire) : pas dans la coquille précachée.
const ACTIVITY_ONLY = ['activity.js'];

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

test('traductions : chaque clé employée dans les scripts de la page existe', () => {
  const code = SCRIPTS.filter((name) => name !== 'i18n.js').map(read).join('\n');   // i18n.js définit les clés (et les cite dans un commentaire)
  const viaTranslator = [...code.matchAll(/\bt\('([^']+)'/g)].map((m) => m[1]);
  // Clés passées par leur nom (endSession('login.expired')…) : toujours de la forme « domaine.nom ».
  const named = [...code.matchAll(/'((?:login|voice|menu|sheet|tabs|empty|search|section|sound|preview|load|editor|drop)\.[A-Za-z.]+)'/g)].map((m) => m[1]);
  assert.ok(viaTranslator.length > 50, 'des clés sont employées');
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
  for (const needed of [...SCRIPTS.filter((name) => !ACTIVITY_ONLY.includes(name)), 'app.css', 'manifest.webmanifest']) assert.ok(shell.includes(`/app/${needed}`), `${needed} devrait être précaché`);
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
  for (const name of [...SCRIPTS, 'sw.js']) {
    const code = read(name);
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\(/.test(code), `${name} : API dangereuse`);
    assert.ok(!/setAttribute\(\s*['"]style['"]/.test(code), `${name} : style posé par attribut (bloqué par la CSP)`);
  }
  for (const name of SCRIPTS) assert.ok(!/console\.(log|info|debug)/.test(read(name)), `${name} : pas de journal dans la console`);
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
  const calls = [...SCRIPTS.map(read).join('\n').matchAll(/api\(`?'?(\/[a-z-]+)/g)].map((m) => m[1]);
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

test('Activité Discord : kit officiel embarqué, intact, chargé seulement à la demande', async () => {
  const sdk = readFileSync(join(WEB, 'vendor', 'discord-embedded-app-sdk.js'));
  const readme = read('vendor/README.txt');
  const recorded = /SHA-256 : ([0-9a-f]{64})/.exec(readme);
  assert.ok(recorded, 'empreinte notée dans vendor/README.txt');
  const { createHash } = await import('node:crypto');
  assert.equal(createHash('sha256').update(sdk).digest('hex'), recorded[1], 'le kit embarqué a été modifié sans mettre à jour l\'empreinte');
  const text = sdk.toString('utf8');
  assert.match(text, /export\{[^}]*as DiscordSDK/, 'exporte DiscordSDK');
  assert.ok(!/\beval\(|new Function\(|innerHTML|document\.write/.test(text), 'API dangereuse dans le kit');
  assert.ok(sdk.length > 50_000 && sdk.length < 400_000, `taille inattendue : ${sdk.length}`);
  assert.ok(existsSync(join(WEB, 'vendor', 'LICENSE-discord-embedded-app-sdk.txt')), 'licence MIT jointe');
  assert.match(read('vendor/LICENSE-discord-embedded-app-sdk.txt'), /MIT License/);
  // Le kit ne doit être importé que par activity.js, et à la demande (import dynamique) : jamais par la page ordinaire.
  for (const name of SCRIPTS) {
    const code = read(name);
    if (name === 'activity.js') assert.match(code, /await import\('\.\/vendor\/discord-embedded-app-sdk\.js'\)/);
    else assert.ok(!code.includes('vendor/discord-embedded-app-sdk'), `${name} ne doit pas charger le kit`);
  }
  assert.ok(!read('sw.js').includes('vendor'), 'le service worker ne met pas le kit en cache');
});
