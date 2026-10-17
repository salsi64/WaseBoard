import { loadJson, prefsEqual, prefsToWire, sanitizePrefs, saveJson } from './logic.js';

// Les préférences de la personne (favoris, couleurs, catégories personnelles) : gardées dans ce navigateur pour s'afficher tout de suite et
// hors connexion, et synchronisées avec le serveur, qui les partage avec l'application Windows et les autres appareils.
//
// À chaque échange, la page envoie son état ET l'état du serveur qu'elle avait reçu la fois d'avant (« base ») ; le serveur fusionne
// (server/preferences.py) et renvoie le résultat. On n'adopte ce résultat que si rien n'a changé pendant l'échange : sinon la base ne bouge
// pas et l'échange suivant fusionne le tout (renvoyer des modifications déjà prises en compte est sans effet).
//
// Aucun accès au navigateur ici hors du stockage qu'on lui passe : testé tel quel avec Node (tests/web/prefs.test.mjs).

export const SAVE_DELAY_MS = 600;    // une série de modifications ne fait qu'un envoi
export const RETRY_DELAY_MS = 15000; // après un échec (hors connexion, serveur indisponible)

/**
 * @param api        (chemin, options) → { ok, status, data, offline } : la fonction de requête de la page
 * @param storage    localStorage (ou équivalent) : garde l'état entre deux visites
 * @param userId     identifiant Discord : chaque personne a ses préférences, même sur un navigateur partagé
 * @param legacy     favoris et couleurs d'avant la synchronisation (stockés seuls dans le navigateur) : repris à la première synchronisation
 * @param onChange   appelé à chaque modification locale ou reçue : la page se redessine
 * @param onFirstSync appelé une fois, après le premier échange réussi (nettoyage de ce qui précède)
 */
export function createPrefsStore({ api, storage, userId, legacy = null, onChange = () => {}, onFirstSync = () => {}, setTimer = setTimeout, clearTimer = clearTimeout }) {
  const key = `wb.prefs.${userId}`;
  const saved = loadJson(storage, key, null);
  let local = sanitizePrefs(saved && saved.local);
  let base = saved && saved.base ? sanitizePrefs(saved.base) : null;
  if (!saved && legacy) local = sanitizePrefs({ favorites: legacy.favorites, colors: legacy.colors });
  let inFlight = false;
  let again = false;
  let timer = null;
  let stopped = false;
  let firstDone = base !== null;

  const persist = () => saveJson(storage, key, { local: prefsToWire(local), base: base ? prefsToWire(base) : null });
  const schedule = (delay) => {
    if (stopped) return;
    clearTimer(timer);
    timer = setTimer(() => { timer = null; sync(); }, delay);
  };

  async function sync() {
    if (stopped) return { ok: false };
    if (inFlight) { again = true; return { ok: false, busy: true }; }
    inFlight = true;
    const sent = local;
    let outcome = { ok: false };
    try {
      const result = await api('/me/preferences/sync', { method: 'POST', body: { base: base ? prefsToWire(base) : null, doc: prefsToWire(sent) } });
      if (stopped) return outcome;
      if (!result.ok || !result.data || typeof result.data.doc !== 'object') {
        outcome = { ok: false, offline: Boolean(result.offline), status: result.status };
        if (base === null || !prefsEqual(local, base)) schedule(RETRY_DELAY_MS);   // des modifications attendent encore d'être envoyées
        return outcome;
      }
      const merged = sanitizePrefs(result.data.doc);
      if (prefsEqual(local, sent)) {
        const changed = !prefsEqual(local, merged);
        local = merged;
        base = merged;
        persist();
        if (changed) onChange();
      } else {
        again = true;   // modifié pendant l'échange : on ne touche pas à la base, le prochain échange fusionnera
      }
      outcome = { ok: true };
      if (!firstDone) { firstDone = true; onFirstSync(); }
    } finally {
      inFlight = false;
      if (again && !stopped) { again = false; schedule(0); }
    }
    return outcome;
  }

  return {
    get prefs() { return local; },
    /** Y a-t-il des modifications pas encore confirmées par le serveur ? */
    get pending() { return base === null || !prefsEqual(local, base); },
    /** Applique une modification (fonction prefs → prefs) : visible tout de suite, enregistrée dans le navigateur, envoyée dans un instant. */
    edit(change) {
      const next = change(local);
      if (next === local) return;
      local = next;
      persist();
      onChange();
      schedule(SAVE_DELAY_MS);
    },
    /** Échange avec le serveur maintenant (au chargement, au retour de la page, périodiquement : récupère ce qui a changé ailleurs). */
    sync,
    stop() { stopped = true; clearTimer(timer); },
  };
}
