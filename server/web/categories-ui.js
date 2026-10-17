import { closeOverlay, confirmDialog, h, openOverlay } from './dom.js';
import { CATEGORY_NAME_MAX, addCategory, categoryPosition, checkCategoryName, moveCategory, removeCategory, renameCategory, setMembership } from './logic.js';

// Gestion des catégories personnelles depuis la page : créer, renommer, supprimer, déplacer, y mettre ou en retirer des sons. Les
// modifications passent par `edit` (le magasin de préférences : visibles tout de suite, envoyées au serveur dans un instant).
// Pas de window.prompt / confirm : une Activité Discord (iframe) peut les bloquer, d'où ces petites fenêtres.

const ERROR_KEYS = { empty: 'category.error.empty', long: 'category.error.long', exists: 'category.error.exists' };

export function createCategoryUi({ t, getState, edit }) {
  /** Demande un nom de catégorie : Promise<string | null> (null si on annule). */
  function promptName({ title, ok, value = '', current = null }) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (result) => {
        if (done) return;
        done = true;
        closeOverlay(overlay);
        resolve(result);
      };
      const error = h('p', { class: 'form-error', role: 'alert' });
      const submit = () => {
        const checked = checkCategoryName(getState().prefs, input.value, current);
        if (checked.error) { error.textContent = t(ERROR_KEYS[checked.error]); input.focus(); return; }
        finish(checked.name);
      };
      const input = h('input', {
        type: 'text', id: 'category-name', maxlength: CATEGORY_NAME_MAX, value, autocomplete: 'off', 'aria-label': t('category.name.label'),
        onkeydown: (event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } },
      });
      const overlay = h('div', { class: 'overlay confirm', role: 'presentation', onclick: (event) => { if (event.target === overlay) finish(null); } },
        h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
          h('h2', { text: title }),
          h('div', { class: 'field' }, h('label', { class: 'label', for: 'category-name', text: t('category.name.label') }), input, error),
          h('div', { class: 'actions' },
            h('button', { class: 'btn', onclick: () => finish(null), text: t('editor.cancel') }),
            h('button', { class: 'btn primary', id: 'category-ok', onclick: submit, text: ok }))));
      overlay.addEventListener('wb-close', () => finish(null));
      openOverlay(overlay, { focus: false });
      input.focus();
      input.select();
    });
  }

  /** Crée une catégorie (nom demandé) ; renvoie son nom, ou null si on a annulé. */
  async function createCategory() {
    const name = await promptName({ title: t('category.new.title'), ok: t('category.create') });
    if (name) edit((prefs) => addCategory(prefs, name));
    return name;
  }

  /** Options d'une catégorie : renommer, déplacer, supprimer. */
  function openCategorySheet(name) {
    const state = getState();
    const position = categoryPosition(state.prefs, state.guilds, name);
    const rename = async () => {
      closeOverlay();
      const next = await promptName({ title: t('category.rename.title'), ok: t('category.save'), value: name, current: name });
      if (next && next !== name) edit((prefs) => renameCategory(prefs, name, next));
    };
    const remove = async () => {
      closeOverlay();
      if (await confirmDialog(t('category.delete.confirm', name), { ok: t('category.delete'), cancel: t('editor.cancel'), danger: true })) {
        edit((prefs) => removeCategory(prefs, name));
      }
    };
    const move = (delta) => { closeOverlay(); edit((prefs) => moveCategory(prefs, getState().guilds, name, delta)); };
    const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': name },
      h('h2', {}, h('span', { class: 'emoji', 'aria-hidden': 'true', text: '📁' }), h('span', { text: name })),
      h('button', { class: 'btn row', id: 'category-rename', onclick: rename, text: `✏️ ${t('category.rename')}` }),
      h('button', { class: 'btn row', id: 'category-up', disabled: position.first, onclick: () => move(-1), text: `⬆️ ${t('category.moveUp')}` }),
      h('button', { class: 'btn row', id: 'category-down', disabled: position.last, onclick: () => move(1), text: `⬇️ ${t('category.moveDown')}` }),
      h('button', { class: 'btn row danger', id: 'category-delete', onclick: remove, text: `🗑️ ${t('category.delete')}` }),
      h('button', { class: 'btn row', onclick: closeOverlay, text: t('sheet.close') }));
    const overlay = h('div', { class: 'overlay', onclick: (event) => { if (event.target === overlay) closeOverlay(); } }, sheet);
    openOverlay(overlay);
  }

  /** Catégories d'un son : une case par catégorie, et la création d'une nouvelle catégorie qui reçoit tout de suite ce son. */
  function openCategoryPicker(sound) {
    const list = h('div', { class: 'share-list' });
    const error = h('p', { class: 'form-error', role: 'alert' });
    const input = h('input', {
      type: 'text', id: 'picker-new', maxlength: CATEGORY_NAME_MAX, placeholder: t('picker.newPlaceholder'), autocomplete: 'off', 'aria-label': t('picker.newPlaceholder'),
      onkeydown: (event) => { if (event.key === 'Enter') { event.preventDefault(); create(); } },
    });
    const draw = () => {
      const { categories } = getState().prefs;
      list.replaceChildren(...(categories.length === 0
        ? [h('p', { class: 'hint', text: t('picker.none') })]
        : categories.map((category) => h('label', { class: 'share' },
          h('input', {
            type: 'checkbox', checked: category.ids.includes(sound.id),
            onchange: (event) => edit((prefs) => setMembership(prefs, category.name, sound.id, event.target.checked)),
          }),
          h('span', { class: 'emoji-small', 'aria-hidden': 'true', text: '📁' }),
          h('span', { class: 'share-name', text: category.name })))));
    };
    function create() {
      const checked = checkCategoryName(getState().prefs, input.value);
      if (checked.error) { error.textContent = t(ERROR_KEYS[checked.error]); input.focus(); return; }
      error.textContent = '';
      edit((prefs) => setMembership(addCategory(prefs, checked.name), checked.name, sound.id, true));
      input.value = '';
      draw();
    }
    const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('picker.title', sound.name) },
      h('h2', {}, h('span', { class: 'emoji', 'aria-hidden': 'true', text: sound.emoji || '🔊' }), h('span', { text: t('picker.title', sound.name) })),
      list,
      h('div', { class: 'field new-category' }, input, h('button', { class: 'btn', id: 'picker-create', onclick: create, text: t('picker.create') }), error),
      h('button', { class: 'btn row', onclick: closeOverlay, text: t('sheet.close') }));
    const overlay = h('div', { class: 'overlay', onclick: (event) => { if (event.target === overlay) closeOverlay(); } }, sheet);
    draw();
    openOverlay(overlay);
  }

  return { createCategory, openCategorySheet, openCategoryPicker };
}
