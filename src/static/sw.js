/* NLC Dashboard service worker.
 *
 * Purpose: make the site an installable app (its own window + icon on the
 * taskbar/Start menu). It deliberately does NOT cache pages — this is a live,
 * auth'd dashboard, so stale or another-user's cached HTML would be wrong.
 * Only the static brand assets (icons/manifest) are cached; everything else
 * goes straight to the network. If the network is down, we surface that rather
 * than serving something misleading from cache.
 */
const CACHE = 'nlc-dashboard-static-v1';
const SHELL = [
  '/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/icon-maskable-512.png',
  '/static/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // never touch POSTs (imports, edits, deletes)

  const url = new URL(req.url);
  const isStaticAsset =
    url.origin === self.location.origin &&
    (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest');

  if (isStaticAsset) {
    // Cache-first for immutable brand assets.
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req))
    );
    return;
  }
  // Everything else (pages, data, charts): always live from the network.
  event.respondWith(fetch(req));
});
