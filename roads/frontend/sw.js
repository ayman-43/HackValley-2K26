/* Nagrik Roads app service worker: makes the phone app installable and lets its screens open offline.
   API calls and uploads always go to the network (never cached). */
const CACHE = 'roadsense-shell-v1';
const SHELL = ['/app', '/live.html', '/styles.css', '/rsmap.js', '/assets/logo.svg', '/assets/icon-192.png', '/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/') || url.pathname.startsWith('/media/')) return;
  // network first, so the latest version is always used when online; cached shell when offline
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && SHELL.includes(url.pathname)) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return res;
  }).catch(() => caches.match(e.request)));
});
