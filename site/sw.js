// TradeReady service worker: opens fast and still shows the last scan when the network drops.
// Pages and scan data: network first, saved copy if offline. Icons and fonts: saved copy first.
const CACHE = "tr-v1";
self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (url.origin === location.origin && (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/") || url.pathname.startsWith("/kite/") || url.pathname === "/logout")) return;
  const isStatic = /\.(png|svg|webmanifest|woff2?)$/.test(url.pathname) || url.hostname.includes("fonts.g");
  if (isStatic) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => { if (res.ok) caches.open(CACHE).then(c => c.put(req, res.clone())); return res; })));
    return;
  }
  if (url.origin !== location.origin) return;
  const key = url.pathname.startsWith("/data/") ? new Request(url.origin + url.pathname) : req;   // ignore the ?t= cache-buster
  e.respondWith(fetch(req).then(res => {
    if (res.ok && !res.redirected) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
    return res;
  }).catch(() => caches.match(key).then(hit => hit || caches.match("/"))));
});
