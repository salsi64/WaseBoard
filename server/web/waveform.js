// Éditeur de découpe : décodage du son par le navigateur, forme d'onde sur un canvas, deux poignées (souris, doigt, clavier),
// écoute de l'extrait. La partie calcul est dans editor-logic.js.
import { h } from './dom.js';
import { MAX_DECODE_BYTES, MIN_TRIM_SECONDS, PEAK_BUCKETS, clampSelection, computePeaks, formatClock, moveHandle } from './editor-logic.js';

let audioContext = null;

function getAudioContext() {
  if (!audioContext) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error('Web Audio indisponible');
    audioContext = new Context();
  }
  return audioContext;
}

/** Décode un son (ArrayBuffer) : { buffer, duration, peaks }. Lève une exception si le navigateur ne sait pas le lire. */
export async function decodeAudio(arrayBuffer) {
  if (arrayBuffer.byteLength > MAX_DECODE_BYTES) throw new Error('trop lourd');
  const context = getAudioContext();
  const buffer = await context.decodeAudioData(arrayBuffer.slice(0));
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, i) => buffer.getChannelData(i));
  return { buffer, duration: buffer.duration, peaks: computePeaks(channels, PEAK_BUCKETS) };
}

/**
 * Crée l'éditeur de découpe. `labels` : textes déjà traduits { start, end, play, stop, all, hint }.
 * Renvoie { element, setAudio(decoded, selection), getSelection(), stop(), destroy() } ; `onChange(selection)` à chaque
 * déplacement de poignée.
 */
export function createTrimEditor({ labels, onChange }) {
  let decoded = null;
  let selection = { start: 0, end: 0 };
  let source = null;
  let frame = 0;

  const canvas = h('canvas', { class: 'wave-canvas', 'aria-hidden': 'true' });
  const shadeStart = h('div', { class: 'wave-shade' });
  const shadeEnd = h('div', { class: 'wave-shade end' });
  const playhead = h('div', { class: 'playhead', hidden: true });
  const handle = (which, label) => h('button', {
    type: 'button', class: `handle ${which}`, role: 'slider', 'aria-label': label, 'aria-valuemin': '0', 'data-which': which,
  });
  const startHandle = handle('start', labels.start);
  const endHandle = handle('end', labels.end);
  const wave = h('div', { class: 'wave' }, canvas, shadeStart, shadeEnd, playhead, startHandle, endHandle);
  const startText = h('span', { class: 'time' });
  const lengthText = h('span', { class: 'time length' });
  const endText = h('span', { class: 'time' });
  const playButton = h('button', { type: 'button', class: 'btn', onclick: () => (source ? stop() : play()), text: `▶︎ ${labels.play}` });
  const allButton = h('button', { type: 'button', class: 'btn', onclick: () => { stop(); apply({ start: 0, end: decoded.duration }); }, text: labels.all });
  const element = h('div', { class: 'trim' },
    wave,
    h('div', { class: 'trim-times' }, startText, lengthText, endText),
    h('div', { class: 'trim-actions' }, playButton, allButton),
    h('p', { class: 'hint', text: labels.hint }));

  function percent(seconds) {
    return decoded && decoded.duration > 0 ? (100 * seconds) / decoded.duration : 0;
  }

  function layout() {
    if (!decoded) return;
    const from = percent(selection.start);
    const to = percent(selection.end);
    startHandle.style.left = `${from}%`;
    endHandle.style.left = `${to}%`;
    shadeStart.style.width = `${from}%`;
    shadeEnd.style.width = `${100 - to}%`;
    startHandle.setAttribute('aria-valuemax', String(decoded.duration));
    startHandle.setAttribute('aria-valuenow', selection.start.toFixed(2));
    endHandle.setAttribute('aria-valuemax', String(decoded.duration));
    endHandle.setAttribute('aria-valuenow', selection.end.toFixed(2));
    startText.textContent = formatClock(selection.start);
    endText.textContent = formatClock(selection.end);
    lengthText.textContent = `${(selection.end - selection.start).toFixed(1)} s`;
  }

  function draw() {
    if (!decoded) return;
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(wave.clientWidth * ratio));
    const height = Math.max(1, Math.round(wave.clientHeight * ratio));
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, width, height);
    context.fillStyle = getComputedStyle(wave).getPropertyValue('--wave-color').trim() || '#38bdf8';
    const barWidth = Math.max(1, Math.floor(width / 150));
    const step = barWidth * 2;
    const middle = height / 2;
    for (let x = 0; x < width; x += step) {
      const peak = decoded.peaks[Math.min(decoded.peaks.length - 1, Math.floor((x / width) * decoded.peaks.length))];
      const barHeight = Math.max(2 * ratio, peak * height * 0.9);
      context.fillRect(x, middle - barHeight / 2, barWidth, barHeight);
    }
  }

  function apply(next, notify = true) {
    selection = next;
    layout();
    if (notify && onChange) onChange(selection);
  }

  // ---------- Poignées : souris, doigt, clavier ----------
  function timeAt(event) {
    const rect = wave.getBoundingClientRect();
    const ratio = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0;
    return Math.min(Math.max(ratio, 0), 1) * decoded.duration;
  }

  function startDrag(which, event) {
    if (!decoded) return;
    event.preventDefault();
    stop();
    const target = which === 'start' ? startHandle : endHandle;
    target.setPointerCapture(event.pointerId);
    target.focus();
    const move = (moveEvent) => apply(moveHandle(selection, which, timeAt(moveEvent), decoded.duration));
    const end = () => {
      target.removeEventListener('pointermove', move);
      target.removeEventListener('pointerup', end);
      target.removeEventListener('pointercancel', end);
    };
    target.addEventListener('pointermove', move);
    target.addEventListener('pointerup', end);
    target.addEventListener('pointercancel', end);
  }

  for (const [which, button] of [['start', startHandle], ['end', endHandle]]) {
    button.addEventListener('pointerdown', (event) => startDrag(which, event));
    button.addEventListener('keydown', (event) => {
      if (!decoded) return;
      const step = event.shiftKey ? 0.5 : 0.05;
      const current = which === 'start' ? selection.start : selection.end;
      let next = null;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = current - step;
      else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = current + step;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = decoded.duration;
      if (next === null) return;
      event.preventDefault();
      apply(moveHandle(selection, which, next, decoded.duration));
    });
  }

  // Un appui sur la forme d'onde déplace la poignée la plus proche, qu'on peut alors faire glisser.
  wave.addEventListener('pointerdown', (event) => {
    if (!decoded || event.target.classList.contains('handle')) return;
    const time = timeAt(event);
    const which = Math.abs(time - selection.start) <= Math.abs(time - selection.end) ? 'start' : 'end';
    apply(moveHandle(selection, which, time, decoded.duration));
    startDrag(which, { ...event, pointerId: event.pointerId, preventDefault: () => event.preventDefault() });
  });

  // ---------- Écoute de l'extrait ----------
  function stop() {
    cancelAnimationFrame(frame);
    if (source) {
      source.onended = null;
      try { source.stop(); } catch { /* déjà arrêté */ }
      source = null;
    }
    playhead.hidden = true;
    playButton.textContent = `▶︎ ${labels.play}`;
  }

  async function play() {
    if (!decoded) return;
    const context = getAudioContext();
    if (context.state === 'suspended') await context.resume();
    stop();
    source = context.createBufferSource();
    source.buffer = decoded.buffer;
    source.connect(context.destination);
    const startedAt = context.currentTime;
    const length = selection.end - selection.start;
    source.onended = stop;
    source.start(0, selection.start, length);
    playButton.textContent = `⏹︎ ${labels.stop}`;
    playhead.hidden = false;
    const tick = () => {
      if (!source) return;
      const elapsed = context.currentTime - startedAt;
      playhead.style.left = `${percent(selection.start + Math.min(elapsed, length))}%`;
      frame = requestAnimationFrame(tick);
    };
    tick();
  }

  const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => draw()) : null;
  if (observer) observer.observe(wave);

  return {
    element,
    /** Affiche un son décodé avec la portion donnée (le son entier par défaut). */
    setAudio(audio, initial) {
      stop();
      decoded = audio;
      selection = clampSelection(initial || { start: 0, end: audio.duration }, audio.duration, MIN_TRIM_SECONDS);
      draw();
      layout();
    },
    getSelection: () => ({ ...selection }),
    stop,
    destroy() {
      stop();
      if (observer) observer.disconnect();
    },
  };
}
