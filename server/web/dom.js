// Outils DOM communs à la page : construction d'éléments, messages, fenêtres superposées. Aucune insertion de HTML : tout ce
// qui vient de données (noms de sons, pseudos) passe par du texte.

/** h('div', { class: 'x', onclick: fn, vars: { '--c': '#fff' } }, enfants…) : crée un élément ; les valeurs vides sont ignorées. */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key === 'vars') for (const [name, v] of Object.entries(value)) el.style.setProperty(name, v);   // CSSOM : permis par la CSP
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function avatar(url, size) {
  return url ? h('img', { src: url, alt: '', width: size, height: size, referrerpolicy: 'no-referrer', loading: 'lazy' }) : null;
}

export function toast(message, kind = 'info') {
  const layer = document.getElementById('toasts');
  if (!layer) return;
  const el = h('div', { class: `toast ${kind}`, text: message });
  layer.append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 5000 : 3000);
}

// ---------- Fenêtres superposées (menu, feuille d'options, éditeur, confirmations) : une pile, la plus haute se ferme d'abord ----------
const stack = [];

export function openOverlay(node, { focus = true } = {}) {
  const opener = document.activeElement;
  stack.push({ node, opener });
  document.body.append(node);
  if (focus) {
    const first = node.querySelector('[autofocus], button, input, select');
    if (first) first.focus();
  }
  return node;
}

/** Ferme la fenêtre indiquée, ou la plus haute. Rend le focus à l'élément qui l'avait ouverte. */
export function closeOverlay(node) {
  const index = node ? stack.findIndex((entry) => entry.node === node) : stack.length - 1;
  if (index < 0) return;
  const [entry] = stack.splice(index, 1);
  entry.node.dispatchEvent(new CustomEvent('wb-close'));
  entry.node.remove();
  if (entry.opener && entry.opener.isConnected && entry.opener.focus) entry.opener.focus();
}

export function closeAllOverlays() {
  while (stack.length) closeOverlay();
}

export function hasOverlay() {
  return stack.length > 0;
}

/** Boîte de confirmation : Promise<boolean>. */
export function confirmDialog(message, { ok, cancel, danger = false }) {
  return new Promise((resolve) => {
    let answered = false;
    const answer = (value) => {
      if (answered) return;
      answered = true;
      closeOverlay(overlay);
      resolve(value);
    };
    const overlay = h('div', { class: 'overlay confirm', role: 'presentation', onclick: (event) => { if (event.target === overlay) answer(false); } },
      h('div', { class: 'sheet', role: 'alertdialog', 'aria-modal': 'true', 'aria-label': message },
        h('p', { class: 'confirm-text', text: message }),
        h('div', { class: 'actions' },
          h('button', { class: 'btn', onclick: () => answer(false), text: cancel }),
          h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => answer(true), text: ok }))));
    overlay.addEventListener('wb-close', () => answer(false));   // Échap : refus
    openOverlay(overlay);
  });
}
