// Service worker de la page web WaseBoard : la coquille de la page (HTML, scripts, styles, icônes) reste disponible hors
// connexion et se met à jour toute seule. Il ne touche JAMAIS à l'API (sons, lecture, session…) : ces requêtes passent
// directement par le réseau, rien de personnel n'est mis en cache ici.
//
// Le serveur remplace __BUILD__ par l'empreinte de tous les fichiers de la page : dès qu'un fichier change, ce fichier-ci
// change aussi, le navigateur installe la nouvelle version et l'ancien cache est supprimé.
const BUILD = '__BUILD__';
const CACHE = `wb-web-${BUILD}`;
const SHELL = [
  '/app/', '/app/app.css', '/app/app.js', '/app/logic.js', '/app/i18n.js', '/app/dom.js', '/app/editor.js', '/app/editor-logic.js', '/app/waveform.js', '/app/prefs.js', '/app/categories-ui.js',
  '/app/manifest.webmanifest',
  '/app/icons/icon-192.png', '/app/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith('wb-web-') && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  // Seulement les fichiers de la page. La configuration de connexion (/app/api/…) et toute l'API restent au réseau.
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/app/') || url.pathname.startsWith('/app/api/')) return;
  event.respondWith(networkFirst(request));
});

// Réseau d'abord (la page est toujours à jour), cache en secours (hors connexion).
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request, { ignoreSearch: true });
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = await caches.match('/app/');
      if (shell) return shell;
    }
    throw error;
  }
}
