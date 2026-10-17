// Logique pure de l'éditeur de sons (server/web/editor-logic.js). Lancer : node --test server/tests/web/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ALLOWED_EXTENSIONS, EMOJI_CHOICES, MAX_UPLOAD_BYTES, audioFiles, clampSelection, computePeaks, defaultNameFromFile, defaultUploadGuild,
  diffShares, extensionOf, findDuplicate, firstGrapheme, formatClock, isAllowedFilename, isEmoji, isFullRange, moveHandle, patchBody,
  selectionFromSound, selectionToTrim, sha256Hex, shareChoices, uploadFields, uploadableGuilds, validateForm,
} from '../../web/editor-logic.js';

const near = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} ≉ ${expected}`);

test('fichiers : extensions acceptées comme par le serveur, noms proposés', () => {
  assert.deepEqual(ALLOWED_EXTENSIONS, ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.wma']);
  assert.equal(extensionOf('Rire.MP3'), '.mp3');
  assert.equal(extensionOf('archive.tar.gz'), '.gz');
  assert.equal(extensionOf('sans-extension'), '');
  assert.equal(extensionOf('.cache'), '.cache');
  assert.equal(extensionOf(null), '');
  assert.ok(isAllowedFilename('a.WAV') && isAllowedFilename('chemin/b.flac') && !isAllowedFilename('c.txt') && !isAllowedFilename('d') && !isAllowedFilename('e.mp3.exe'));
  assert.equal(defaultNameFromFile('C:\\sons\\Vine boom.mp3'), 'Vine boom');
  assert.equal(defaultNameFromFile('/tmp/mon.son.v2.wav'), 'mon.son.v2');
  assert.equal(defaultNameFromFile('.mp3'), '.mp3', 'un nom qui ne ressemble qu\'à une extension est gardé');
  assert.equal(defaultNameFromFile('   '), 'Son');
  assert.equal(defaultNameFromFile('x'.repeat(300) + '.mp3').length, 100);
  const files = [{ name: 'a.mp3' }, { name: 'b.txt' }, { name: 'c.OGG' }];
  assert.deepEqual(audioFiles(files).map((f) => f.name), ['a.mp3', 'c.OGG']);
  assert.deepEqual(audioFiles(null), []);
});

test('empreinte SHA-256 : celle que calcule le serveur (vecteur connu)', async () => {
  const bytes = new TextEncoder().encode('abc');
  assert.equal(await sha256Hex(bytes, globalThis.crypto.subtle), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});

test('forme d\'onde : crêtes par tranche, normalisées', () => {
  const left = new Float32Array(1000);
  const right = new Float32Array(1000);
  left[10] = 0.5;            // tranche 0
  right[510] = -1;           // tranche 5 (sur 10), canal droit : la plus forte
  left[999] = 0.25;          // dernière tranche
  const peaks = computePeaks([left, right], 10);
  assert.equal(peaks.length, 10);
  near(peaks[5], 1);
  near(peaks[0], 0.5);
  near(peaks[9], 0.25);
  assert.equal(peaks[3], 0);
  assert.ok([...peaks].every((v) => v >= 0 && v <= 1));
  assert.equal(computePeaks([], 5).length, 5);
  assert.ok(computePeaks([new Float32Array(100)], 10).every((v) => v === 0), 'silence : pas de division par zéro');
  const short = computePeaks([Float32Array.from([0.2, -0.4, 0.1])], 10);
  assert.equal(short.length, 10);
  assert.ok(Math.max(...short) === 1 && [...short].every((v) => Number.isFinite(v)), 'moins d\'échantillons que de tranches');
});

test('portion gardée : bornes, longueur minimale, poignées qui ne se croisent pas', () => {
  assert.deepEqual(clampSelection({ start: -2, end: 99 }, 10), { start: 0, end: 10 });
  assert.deepEqual(clampSelection({ start: 4, end: 4 }, 10), { start: 4, end: 4.1 });
  const atEnd = clampSelection({ start: 10, end: 10 }, 10);
  near(atEnd.start, 9.9); near(atEnd.end, 10);
  assert.deepEqual(clampSelection({ start: NaN, end: NaN }, 5), { start: 0, end: 5 });
  assert.deepEqual(clampSelection({ start: 0, end: 1 }, 0.05), { start: 0, end: 0.05 }, 'son plus court que le minimum');

  const sel = { start: 2, end: 6 };
  assert.deepEqual(moveHandle(sel, 'start', 3, 10), { start: 3, end: 6 });
  near(moveHandle(sel, 'start', 9, 10).start, 5.9);          // ne dépasse pas la fin
  assert.equal(moveHandle(sel, 'start', -5, 10).start, 0);
  near(moveHandle(sel, 'end', 1, 10).end, 2.1);               // ne passe pas sous le début
  assert.equal(moveHandle(sel, 'end', 50, 10).end, 10);
  assert.deepEqual(moveHandle(sel, 'end', 7, 10), { start: 2, end: 7 });
});

test('son entier = aucune découpe enregistrée ; sinon millisecondes arrondies', () => {
  assert.ok(isFullRange({ start: 0, end: 10 }, 10));
  assert.ok(isFullRange({ start: 0.019, end: 9.985 }, 10), 'à 20 ms près');
  assert.ok(!isFullRange({ start: 0.05, end: 10 }, 10));
  assert.equal(selectionToTrim({ start: 0, end: 10 }, 10), null);
  assert.deepEqual(selectionToTrim({ start: 1.2344, end: 3.5006 }, 10), { trim_start_ms: 1234, trim_end_ms: 3501 });
  assert.deepEqual(selectionFromSound({ trim_start_ms: 500, trim_end_ms: 2500 }, 10), { start: 0.5, end: 2.5 });
  assert.deepEqual(selectionFromSound({}, 10), { start: 0, end: 10 });
  assert.deepEqual(selectionFromSound({ trim_start_ms: 9000, trim_end_ms: 20000 }, 10), { start: 9, end: 10 }, 'bornée à la durée réelle');
  assert.equal(formatClock(3.24), '0:03.2');
  assert.equal(formatClock(75.06), '1:15.1');
  assert.equal(formatClock(-4), '0:00.0');
  assert.equal(formatClock(NaN), '0:00.0');
});

test('emoji : un seul caractère visible, reconnu comme emoji', () => {
  for (const ok of ['😀', '👏', '❤️', '🏎️', '🇫🇷', '👨‍👩‍👧', '👍🏽', '1️⃣']) assert.ok(isEmoji(ok), ok);
  for (const bad of ['', 'a', 'abc', '12', '   ', '!']) assert.ok(!isEmoji(bad), bad);
  assert.equal(firstGrapheme('😀😂'), '😀');
  assert.equal(firstGrapheme('👨‍👩‍👧 et la suite'), '👨‍👩‍👧');
  assert.equal(firstGrapheme('  '), '');
  assert.ok(EMOJI_CHOICES.length >= 60 && EMOJI_CHOICES.every((e) => isEmoji(e)), 'tous les choix proposés sont des emojis');
  assert.equal(new Set(EMOJI_CHOICES).size, EMOJI_CHOICES.length, 'pas de doublon');
});

test('formulaire : mêmes règles que la fenêtre de l\'application Windows', () => {
  const file = { name: 'rire.mp3', size: 1000 };
  const ok = { mode: 'add', file, emoji: '😂', guildId: '1' };
  assert.equal(validateForm(ok), null);
  assert.equal(validateForm({ ...ok, file: null }).key, 'editor.error.file');
  assert.equal(validateForm({ ...ok, file: { name: 'a.txt', size: 5 } }).key, 'editor.error.format');
  assert.deepEqual(validateForm({ ...ok, file: { name: 'a.txt', size: 5 } }).args, ['.txt', ALLOWED_EXTENSIONS.join(', ')]);
  assert.equal(validateForm({ ...ok, file: { name: 'a.mp3', size: MAX_UPLOAD_BYTES + 1 } }).key, 'editor.error.size');
  assert.equal(validateForm({ ...ok, file: { name: 'a.mp3', size: MAX_UPLOAD_BYTES } }), null, 'pile 64 Mo : accepté');
  assert.equal(validateForm({ ...ok, guildId: '' }).key, 'editor.error.guild');
  assert.equal(validateForm({ ...ok, emoji: '' }).key, 'editor.error.emoji', 'emoji obligatoire à l\'ajout');
  assert.equal(validateForm({ ...ok, emoji: 'abc' }).key, 'editor.error.emojiInvalid');
  assert.equal(validateForm({ ...ok, trimAvailable: true, selection: { start: 1, end: 1.05 }, duration: 10 }).key, 'editor.error.trim');
  assert.equal(validateForm({ ...ok, trimAvailable: true, selection: { start: 1, end: 1.1 }, duration: 10 }), null, '100 ms : accepté');
  assert.equal(validateForm({ ...ok, trimAvailable: false, selection: { start: 1, end: 1.05 }, duration: 10 }), null, 'pas de découpe : rien à vérifier');

  const edit = { mode: 'edit', emoji: '', replacementFile: null };
  assert.equal(validateForm(edit), null, 'édition : l\'emoji peut rester vide (anciens sons)');
  assert.equal(validateForm({ ...edit, emoji: 'texte' }).key, 'editor.error.emojiInvalid');
  assert.equal(validateForm({ ...edit, replacementFile: { name: 'a.exe', size: 1 } }).key, 'editor.error.format');
  assert.equal(validateForm({ ...edit, replacementFile: { name: 'a.wav', size: 1 } }), null);
});

test('envoi : champs du formulaire POST /sounds', () => {
  assert.deepEqual(uploadFields({ name: '  Rire  ', emoji: '😂', guildId: '42', trim: null }), [['name', 'Rire'], ['guild_id', '42'], ['emoji', '😂']]);
  assert.deepEqual(uploadFields({ name: '', emoji: '😂', guildId: '42', trim: { trim_start_ms: 100, trim_end_ms: 900 } }),
    [['name', 'Son'], ['guild_id', '42'], ['emoji', '😂'], ['trim_start_ms', '100'], ['trim_end_ms', '900']]);
});

test('modification : seulement ce qui a changé', () => {
  const original = { name: 'Rire', emoji: '😂', trim: { trim_start_ms: 500, trim_end_ms: 2500 } };
  assert.equal(patchBody(original, { ...original }), null);
  assert.deepEqual(patchBody(original, { ...original, name: 'Gros rire' }), { name: 'Gros rire' });
  assert.deepEqual(patchBody(original, { ...original, emoji: '🤣' }), { emoji: '🤣' });
  assert.equal(patchBody(original, { ...original, trim: { trim_start_ms: 505, trim_end_ms: 2490 } }), null, 'à 10 ms près : inchangé');
  assert.deepEqual(patchBody(original, { ...original, trim: { trim_start_ms: 600, trim_end_ms: 2500 } }), { trim_start_ms: 600, trim_end_ms: 2500 });
  assert.deepEqual(patchBody(original, { ...original, trim: null }), { trim_start_ms: null, trim_end_ms: null }, 'découpe retirée');
  assert.deepEqual(patchBody({ ...original, trim: null }, { ...original, trim: { trim_start_ms: 1, trim_end_ms: 200 } }), { trim_start_ms: 1, trim_end_ms: 200 });
  assert.equal(patchBody({ name: 'a', emoji: '', trim: null }, { name: 'a', emoji: '', trim: null }), null);
  assert.deepEqual(patchBody({ name: 'a', emoji: '😂', trim: null }, { name: 'a', emoji: '', trim: null }), { emoji: '' }, 'emoji retiré');
  assert.deepEqual(patchBody(original, { name: 'B', emoji: '🤣', trim: null }), { name: 'B', emoji: '🤣', trim_start_ms: null, trim_end_ms: null });
});

test('doublons : même contenu ou même nom, dans le serveur visé seulement', () => {
  const sounds = [
    { id: 'a', name: 'Applaudissements', hash: 'H1', guild_id: 'G1' },
    { id: 'b', name: 'Bruh', hash: 'H2', guild_id: 'G2' },
    { id: 'c', name: 'Partagé', hash: 'H3', guild_id: 'G2' },
  ];
  const shared = { G1: ['c'] };
  assert.deepEqual(findDuplicate(sounds, shared, 'G1', { hash: 'H1', name: 'autre nom' }), { kind: 'content', sound: sounds[0] });
  assert.equal(findDuplicate(sounds, shared, 'G1', { hash: 'H2', name: 'x' }), null, 'même contenu dans un AUTRE serveur : pas un doublon ici');
  assert.deepEqual(findDuplicate(sounds, shared, 'G1', { hash: 'H3', name: 'x' }).sound.id, 'c', 'un son partagé dans ce serveur compte');
  assert.deepEqual(findDuplicate(sounds, shared, 'G1', { hash: null, name: '  APPLAUDISSEMENTS ' }), { kind: 'name', sound: sounds[0] });
  assert.equal(findDuplicate(sounds, shared, 'G1', { hash: 'zzz', name: 'Bruh' }), null);
  assert.equal(findDuplicate(sounds, shared, 'G1', { hash: null, name: '' }), null);
  assert.equal(findDuplicate(sounds, undefined, 'G1', { hash: 'H1', name: 'x' }).kind, 'content');
  assert.equal(findDuplicate([{ id: 'x', name: 'N', guild_id: 'G1' }], {}, 'G1', { hash: 'H', name: 'z' }), null, 'son sans empreinte : jamais comparé par contenu');
});

test('serveurs où l\'on peut ajouter, et serveur proposé', () => {
  const guilds = [{ id: 'G1', can_upload: false }, { id: 'G2', can_upload: true }, { id: 'G3', can_upload: true }];
  assert.deepEqual(uploadableGuilds(guilds).map((g) => g.id), ['G2', 'G3']);
  assert.equal(defaultUploadGuild(guilds, { tab: 'G3', voiceGuildId: 'G2' }).id, 'G3', 'l\'onglet ouvert d\'abord');
  assert.equal(defaultUploadGuild(guilds, { tab: 'all', voiceGuildId: 'G3' }).id, 'G3', 'puis le salon vocal');
  assert.equal(defaultUploadGuild(guilds, { tab: 'G1', voiceGuildId: 'G1' }).id, 'G2', 'serveur sans droit d\'ajout : ignoré, premier possible');
  assert.equal(defaultUploadGuild([{ id: 'G1', can_upload: false }], { tab: 'all', voiceGuildId: null }), null);
});

test('catégories partagées : qui peut quoi, et requêtes à faire', () => {
  const guilds = [
    { id: 'G1', name: 'Alpha', is_admin: false, icon_url: 'https://cdn.discordapp.com/i.png' },
    { id: 'G2', name: 'Beta', is_admin: true },
    { id: 'G3', name: 'Gamma', is_admin: false },
  ];
  const shared = { G3: ['s'] };
  const mine = { id: 's', guild_id: 'G1', is_mine: true };
  const choices = shareChoices(mine, guilds, shared);
  assert.deepEqual(choices.map((c) => [c.guildId, c.member, c.locked]), [['G1', true, true], ['G2', false, false], ['G3', true, false]], 'l\'auteur : partout');
  assert.equal(choices[0].iconUrl, 'https://cdn.discordapp.com/i.png');
  const others = { id: 's', guild_id: 'G1', is_mine: false };
  assert.deepEqual(shareChoices(others, guilds, shared).map((c) => c.guildId), ['G2'], 'pas l\'auteur : seulement les serveurs où l\'on est admin');
  assert.deepEqual(shareChoices(mine, guilds, null).map((c) => c.member), [true, false, false]);

  const desired = choices.map((c) => ({ ...c }));
  assert.deepEqual(diffShares(choices, desired), []);
  desired[1].member = true;      // G2 : ajouter
  desired[2].member = false;     // G3 : retirer
  desired[0].member = false;     // G1 (serveur d'origine) : verrouillé, jamais retiré
  assert.deepEqual(diffShares(choices, desired), [{ guild_id: 'G2', action: 'add' }, { guild_id: 'G3', action: 'remove' }]);
});
