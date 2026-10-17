// Éditeur de sons de la page web : ajout (un ou plusieurs fichiers), modification (nom, emoji, découpe, nouveau fichier,
// catégories des serveurs) et suppression. Mêmes règles et même ordre d'opérations que la fenêtre de l'application Windows :
// nouveau fichier d'abord (le serveur retire alors la découpe), puis catégories des serveurs, puis nom / emoji / découpe.
// Les droits sont ceux du serveur, qui refait toutes les vérifications ; ici on ne fait que masquer ce qui serait refusé.
import { avatar, closeOverlay, confirmDialog, h, openOverlay, toast } from './dom.js';
import {
  ALLOWED_EXTENSIONS, EMOJI_CHOICES, MAX_NAME_LENGTH, defaultNameFromFile, defaultUploadGuild, diffShares, findDuplicate,
  firstGrapheme, patchBody, selectionFromSound, selectionToTrim, sha256Hex, shareChoices, uploadFields, uploadableGuilds, validateForm,
} from './editor-logic.js';
import { errorText } from './logic.js';
import { createTrimEditor, decodeAudio } from './waveform.js';

/**
 * @param ctx { t, api, fetchFile(id) → ArrayBuffer|null, getState(), refresh() → Promise, currentTab() }
 */
export function createEditor(ctx) {
  const { t } = ctx;

  /** Ouvre l'éditeur d'ajout pour chaque fichier, l'un après l'autre. */
  async function openAdd(files) {
    for (let i = 0; i < files.length; i++) {
      await runEditor({ mode: 'add', file: files[i], position: files.length > 1 ? t('editor.queue', i + 1, files.length) : null });
    }
  }

  function openEdit(sound) {
    return runEditor({ mode: 'edit', sound });
  }

  function runEditor({ mode, file = null, sound = null, position = null }) {
    const state = ctx.getState();
    return new Promise((resolve) => {
      const isAdd = mode === 'add';
      let busy = false;
      let finished = false;
      let originalAudio = null;          // son actuel (édition) ou fichier choisi (ajout), décodé
      let replacementAudio = null;       // nouveau fichier décodé, s'il y en a un
      let replacementFile = null;
      let trimAvailable = false;
      let originalSelection = null;

      const originalTrim = !isAdd && Number.isInteger(sound.trim_start_ms) && Number.isInteger(sound.trim_end_ms)
        ? { trim_start_ms: sound.trim_start_ms, trim_end_ms: sound.trim_end_ms } : null;
      const original = isAdd ? null : { name: sound.name, emoji: sound.emoji || '', trim: originalTrim };

      const uploadGuilds = uploadableGuilds(state.guilds);
      const startGuild = defaultUploadGuild(state.guilds, { tab: ctx.currentTab(), voiceGuildId: state.voice.guildId });
      const initialShares = isAdd ? [] : shareChoices(sound, state.guilds, state.shared);
      const shares = initialShares.map((choice) => ({ ...choice }));

      // ---------- Champs ----------
      const nameInput = h('input', {
        type: 'text', id: 'ed-name', maxlength: MAX_NAME_LENGTH, autocomplete: 'off', value: isAdd ? defaultNameFromFile(file.name) : sound.name, oninput: refreshState,
      });
      const emojiInput = h('input', { type: 'text', id: 'ed-emoji', class: 'emoji-input', autocomplete: 'off', value: sound ? sound.emoji || '' : '', inputmode: 'text', 'aria-label': t('editor.emoji'), oninput: onEmojiInput });
      const emojiPreview = h('span', { class: 'emoji', 'aria-hidden': 'true' });
      const emojiGrid = h('div', { class: 'emoji-grid', hidden: true }, EMOJI_CHOICES.map((emoji) => h('button', {
        type: 'button', class: 'emoji-choice', text: emoji, 'aria-label': emoji,
        onclick: () => { emojiInput.value = emoji; emojiGrid.hidden = true; onEmojiInput(); },
      })));
      const guildSelect = isAdd ? h('select', { id: 'ed-guild', onchange: refreshState }, uploadGuilds.map((guild) => h('option', {
        value: guild.id, text: guild.name, selected: startGuild && guild.id === startGuild.id,
      }))) : null;
      const errorLine = h('p', { class: 'form-error', role: 'alert', hidden: true });
      const saveButton = h('button', { type: 'button', class: 'btn primary', id: 'ed-save', onclick: save, text: t(isAdd ? 'editor.save.add' : 'editor.save.edit') });
      const cancelButton = h('button', { type: 'button', class: 'btn', onclick: () => closeOverlay(overlay), text: t('editor.cancel') });

      // ---------- Découpe ----------
      const trimEditor = createTrimEditor({
        labels: { start: t('editor.trim.start'), end: t('editor.trim.end'), play: t('editor.trim.play'), stop: t('editor.trim.stop'), all: t('editor.trim.all'), hint: t('editor.trim.hint') },
        onChange: refreshState,
      });
      const trimNote = h('p', { class: 'hint', text: t('editor.trim.loading') });
      const trimBlock = h('section', { class: 'field' }, h('div', { class: 'label', text: t('editor.trim') }), trimNote, trimEditor.element);
      trimEditor.element.hidden = true;

      // ---------- Nouveau fichier (édition) ----------
      const replaceInput = h('input', { type: 'file', accept: audioAccept(), hidden: true, onchange: onReplacementChosen });
      const replaceName = h('span', { class: 'hint' });
      const replaceUndo = h('button', { type: 'button', class: 'btn', hidden: true, onclick: undoReplacement, text: t('editor.replace.undo') });
      const replaceBlock = isAdd ? null : h('section', { class: 'field' },
        h('div', { class: 'label', text: t('editor.file') }),
        h('div', { class: 'row-wrap' }, h('button', { type: 'button', class: 'btn', onclick: () => replaceInput.click(), text: t('editor.replace') }), replaceUndo),
        replaceName, replaceInput);

      // ---------- Catégories des serveurs (édition) ----------
      const sharesBlock = isAdd || shares.length === 0 ? null : h('section', { class: 'field' },
        h('div', { class: 'label', text: t('editor.share.title') }),
        h('p', { class: 'hint', text: t('editor.share.hint') }),
        h('div', { class: 'share-list' }, shares.map((choice) => h('label', { class: 'share' },
          h('input', { type: 'checkbox', checked: choice.member, disabled: choice.locked, onchange: (event) => { choice.member = event.target.checked; refreshState(); } }),
          avatar(choice.iconUrl, 22),
          h('span', { class: 'share-name', text: choice.guildName }),
          choice.locked ? h('span', { class: 'hint', text: t('editor.share.native') }) : null))));

      // ---------- Suppression (édition) ----------
      const deleteButton = isAdd ? null : h('button', { type: 'button', class: 'btn danger-outline', onclick: removeSound, text: `🗑 ${t('editor.delete')}` });

      const title = t(isAdd ? 'editor.add.title' : 'editor.edit.title');
      const panel = h('div', { class: 'sheet editor', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
        h('div', { class: 'editor-head' },
          h('h2', { text: title }),
          h('button', { type: 'button', class: 'icon-btn', 'aria-label': t('editor.cancel'), onclick: () => closeOverlay(overlay), text: '✕' })),
        position ? h('p', { class: 'hint', text: position }) : null,
        isAdd ? h('p', { class: 'file-line' }, h('span', { 'aria-hidden': 'true', text: '🎵 ' }), h('strong', { text: file.name }), h('span', { class: 'hint', text: ` · ${formatBytes(file.size)}` })) : null,
        h('section', { class: 'field' }, h('label', { class: 'label', for: 'ed-name', text: t('editor.name') }), nameInput),
        h('section', { class: 'field' }, h('label', { class: 'label', for: 'ed-emoji', text: t('editor.emoji') }),
          h('div', { class: 'emoji-row' }, emojiPreview, emojiInput, h('button', { type: 'button', class: 'btn', onclick: () => { emojiGrid.hidden = !emojiGrid.hidden; }, text: t('editor.emoji.pick') })),
          emojiGrid),
        isAdd ? h('section', { class: 'field' }, h('label', { class: 'label', for: 'ed-guild', text: t('editor.guild') }), guildSelect) : null,
        trimBlock,
        replaceBlock,
        sharesBlock,
        errorLine,
        h('div', { class: 'actions editor-actions' }, deleteButton, h('span', { class: 'spacer' }), cancelButton, saveButton));

      const overlay = h('div', { class: 'overlay editor-overlay', role: 'presentation' }, panel);
      overlay.addEventListener('wb-close', () => {
        trimEditor.destroy();
        if (!finished) { finished = true; resolve('cancelled'); }
      });

      function finish(result) {
        finished = true;
        closeOverlay(overlay);
        resolve(result);
      }

      // ---------- État du formulaire ----------
      function onEmojiInput() {
        emojiPreview.textContent = firstGrapheme(emojiInput.value);
        refreshState();
      }

      function currentAudio() { return replacementFile ? replacementAudio : originalAudio; }

      function currentTrim() {
        const audio = currentAudio();
        if (audio) return selectionToTrim(trimEditor.getSelection(), audio.duration);
        return replacementFile ? null : originalTrim;   // son illisible : l'ancienne découpe reste telle quelle ; nouveau fichier illisible : aucune
      }

      function edited() {
        return { name: nameInput.value.trim() || 'Son', emoji: firstGrapheme(emojiInput.value), trim: currentTrim() };
      }

      function isDirty() {
        if (isAdd) return true;
        if (replacementFile) return true;
        const base = original;
        return patchBody(base, edited()) !== null || diffShares(initialShares, shares).length > 0;
      }

      function refreshState() {
        if (busy) return;
        saveButton.disabled = !isDirty();
        errorLine.hidden = true;
      }

      function setBusy(value) {
        busy = value;
        for (const control of panel.querySelectorAll('input, select, button')) {
          if (control !== cancelButton) control.disabled = value;
        }
        if (!value) {
          panel.querySelectorAll('.share input').forEach((checkbox, index) => { checkbox.disabled = shares[index].locked; });
          saveButton.disabled = !isDirty();
        }
        saveButton.textContent = value ? t('editor.saving') : t(isAdd ? 'editor.save.add' : 'editor.save.edit');
      }

      function showError(message) {
        errorLine.textContent = message;
        errorLine.hidden = false;
        setBusy(false);
      }

      function failure(result) {
        showError(result.offline ? t('offline') : t('editor.fail', errorText(result.status, result.data)));
      }

      // ---------- Audio ----------
      async function loadAudio() {
        try {
          const bytes = isAdd ? await file.arrayBuffer() : await ctx.fetchFile(sound.id);
          if (!bytes) throw new Error('indisponible');
          originalAudio = await decodeAudio(bytes);
          originalSelection = isAdd ? { start: 0, end: originalAudio.duration } : selectionFromSound(sound, originalAudio.duration);
          trimAvailable = true;
          trimEditor.setAudio(originalAudio, originalSelection);
          trimNote.hidden = true;
          trimEditor.element.hidden = false;
        } catch {
          trimAvailable = false;
          trimNote.textContent = t('editor.trim.unavailable');
        }
        refreshState();
      }

      async function onReplacementChosen() {
        const chosen = replaceInput.files && replaceInput.files[0];
        replaceInput.value = '';
        if (!chosen) return;
        const problem = validateForm({ mode: 'edit', emoji: '', replacementFile: chosen });
        if (problem) return showError(t(problem.key, ...problem.args));
        replacementFile = chosen;
        replacementAudio = null;
        replaceName.textContent = t('editor.replace.chosen', chosen.name);
        replaceUndo.hidden = false;
        trimNote.hidden = false;
        trimNote.textContent = t('editor.trim.loading');
        trimEditor.element.hidden = true;
        try {
          replacementAudio = await decodeAudio(await chosen.arrayBuffer());
          trimEditor.setAudio(replacementAudio);
          trimNote.hidden = true;
          trimEditor.element.hidden = false;
        } catch {
          trimNote.textContent = t('editor.trim.unavailable');
        }
        refreshState();
      }

      function undoReplacement() {
        replacementFile = null;
        replacementAudio = null;
        replaceName.textContent = '';
        replaceUndo.hidden = true;
        if (originalAudio) {
          trimEditor.setAudio(originalAudio, originalSelection);
          trimNote.hidden = true;
          trimEditor.element.hidden = false;
        } else {
          trimNote.hidden = false;
          trimNote.textContent = t('editor.trim.unavailable');
          trimEditor.element.hidden = true;
        }
        refreshState();
      }

      // ---------- Enregistrement ----------
      async function save() {
        if (busy) return;
        const values = edited();
        const audio = currentAudio();
        const problem = validateForm({
          mode, file, emoji: values.emoji, guildId: guildSelect ? guildSelect.value : null, replacementFile,
          selection: audio ? trimEditor.getSelection() : null, duration: audio ? audio.duration : 0, trimAvailable: Boolean(audio),
        });
        if (problem) return showError(t(problem.key, ...problem.args));
        return isAdd ? saveAdd(values) : saveEdit(values);
      }

      async function saveAdd(values) {
        setBusy(true);
        const guildId = guildSelect.value;
        let hash = null;
        try {
          if (window.crypto && window.crypto.subtle) hash = await sha256Hex(await file.arrayBuffer(), window.crypto.subtle);
        } catch { /* contexte non sécurisé ou fichier illisible : seul le nom sera comparé */ }
        const duplicate = findDuplicate(state.sounds, state.shared, guildId, { hash, name: values.name });
        if (duplicate) {
          setBusy(false);
          const message = duplicate.kind === 'content' ? t('editor.duplicate.content', duplicate.sound.name) : t('editor.duplicate.name', duplicate.sound.name);
          if (!(await confirmDialog(message, { ok: t('editor.add.anyway'), cancel: t('editor.cancel') }))) return;
          setBusy(true);
        }
        const form = new FormData();
        for (const [key, value] of uploadFields({ name: values.name, emoji: values.emoji, guildId, trim: values.trim })) form.append(key, value);
        form.append('file', file, file.name);
        const result = await ctx.api('/sounds', { method: 'POST', form });
        if (!result.ok) return failure(result);
        await ctx.refresh();
        toast(t('editor.added', values.name), 'success');
        finish('saved');
      }

      async function saveEdit(values) {
        if (replacementFile && !(await confirmDialog(t('editor.replace.confirm', original.name), { ok: t('editor.replace.ok'), cancel: t('editor.cancel'), danger: true }))) return;
        setBusy(true);
        if (replacementFile) {
          const form = new FormData();
          form.append('file', replacementFile, replacementFile.name);
          const result = await ctx.api(`/sounds/${encodeURIComponent(sound.id)}/file`, { method: 'PUT', form });
          if (!result.ok) return failure(result);
        }
        const shareErrors = [];
        for (const change of diffShares(initialShares, shares)) {
          const result = await ctx.api('/shared-categories/sounds', { method: 'POST', body: { guild_id: change.guild_id, sound_id: sound.id, action: change.action } });
          if (!result.ok) shareErrors.push(result.offline ? t('offline') : errorText(result.status, result.data));
        }
        // Après un remplacement, le serveur a retiré la découpe : la référence est « aucune découpe ».
        const body = patchBody({ ...original, trim: replacementFile ? null : original.trim }, values);
        if (body) {
          const result = await ctx.api(`/sounds/${encodeURIComponent(sound.id)}`, { method: 'PATCH', body });
          if (!result.ok) { await ctx.refresh(); return failure(result); }
        }
        await ctx.refresh();
        if (shareErrors.length > 0) toast(t('editor.share.failed', shareErrors[0]), 'error');
        toast(t('editor.updated', values.name), 'success');
        finish('saved');
      }

      async function removeSound() {
        if (busy) return;
        if (!(await confirmDialog(t('editor.delete.confirm', original.name), { ok: t('editor.delete'), cancel: t('editor.cancel'), danger: true }))) return;
        setBusy(true);
        const result = await ctx.api(`/sounds/${encodeURIComponent(sound.id)}`, { method: 'DELETE' });
        if (!result.ok) return failure(result);
        await ctx.refresh();
        toast(t('editor.deleted', original.name), 'success');
        finish('deleted');
      }

      openOverlay(overlay, { focus: false });   // pas de saisie au premier plan : le clavier d'un téléphone ne doit pas surgir
      panel.setAttribute('tabindex', '-1');
      panel.focus();
      onEmojiInput();
      loadAudio();
    });
  }

  return { openAdd, openEdit };
}

function audioAccept() {
  return ['audio/*', ...ALLOWED_EXTENSIONS].join(',');
}

function formatBytes(bytes) {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} Mo` : `${Math.max(1, Math.round(bytes / 1024))} Ko`;
}

