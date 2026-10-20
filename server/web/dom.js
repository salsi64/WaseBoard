import { cdnFallbackUrl } from './logic.js';

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

// ---------- Icônes : traits simples de 24 × 24, dessinés par le DOM (SVG sans style en ligne : permis par la CSP) ----------
const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  search: ['circle:11,11,6.5', 'path:M16 16l4.5 4.5'],
  refresh: ['path:M20 12a8 8 0 1 1-2.6-5.9', 'path:M20 4.5v4.5h-4.5'],
  speaker: ['path:M4 9.5v5h3.5l4.5 4v-13l-4.5 4z', 'path:M15.6 9.2a4 4 0 0 1 0 5.6', 'path:M18.3 6.6a8 8 0 0 1 0 10.8'],
  stop: ['rect:5.5,5.5,13,13,2.5'],
  tag: ['path:M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7a1 1 0 0 1 .7.3l8.1 8.1a1 1 0 0 1 0 1.4l-7.7 7.7a1 1 0 0 1-1.4 0l-8.1-8.1a1 1 0 0 1-.3-.7z', 'circle:8,8,1.3'],
  plus: ['path:M12 5v14M5 12h14'],
  gear: ['circle:12,12,4.6', 'circle:12,12,1.6', 'path:M12 3.2v2.4M12 18.4v2.4M3.2 12h2.4M18.4 12h2.4M5.8 5.8l1.7 1.7M16.5 16.5l1.7 1.7M5.8 18.2l1.7-1.7M16.5 7.5l1.7-1.7'],
};

/** icon('speaker') : icône à contour (la couleur vient du texte du bouton) ; taille réglée par la feuille de style. */
export function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', `icon icon-${name}`);
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const spec of ICONS[name] || []) {
    const [kind, data] = [spec.slice(0, spec.indexOf(':')), spec.slice(spec.indexOf(':') + 1)];
    const shape = document.createElementNS(SVG_NS, kind);
    if (kind === 'path') shape.setAttribute('d', data);
    else if (kind === 'circle') { const [cx, cy, r] = data.split(','); shape.setAttribute('cx', cx); shape.setAttribute('cy', cy); shape.setAttribute('r', r); }
    else { const [x, y, w, hh, rx] = data.split(','); shape.setAttribute('x', x); shape.setAttribute('y', y); shape.setAttribute('width', w); shape.setAttribute('height', hh); shape.setAttribute('rx', rx); }
    svg.append(shape);
  }
  return svg;
}

export function avatar(url, size) {
  if (!url) return null;
  const image = h('img', { src: url, alt: '', width: size, height: size, referrerpolicy: 'no-referrer', loading: 'lazy' });
  // Dans une Activité, le CDN de Discord n'est joignable que par la correspondance « /discord-cdn » (si elle est déclarée) : on l'essaie une
  // fois, puis on retire l'image plutôt que d'afficher une icône cassée.
  const mapped = document.documentElement.classList.contains('activity') ? cdnFallbackUrl(url) : null;
  if (mapped) {
    image.addEventListener('error', () => {
      if (image.dataset.mapped) { image.remove(); return; }
      image.dataset.mapped = '1';
      image.src = mapped;
    });
  }
  return image;
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
