// TradeReady service worker: opens fast and still shows the last scan when the network drops.
// App page: network first, but if the network is slow (over 3 s) the saved copy opens at once.
// Scan data: network first, saved copy if offline (the page itself shows a saved copy first, then refreshes).
// Icons and fonts: saved copy first.
const CACHE = "tr-v1";
self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
const save = (key, res) => { if (res.ok && !res.redirected) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); } return res; };
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (url.origin === location.origin && (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/") || url.pathname.startsWith("/kite/") || url.pathname === "/logout")) return;
  const isStatic = /\.(png|jpg|svg|webmanifest|woff2?)$/.test(url.pathname) || url.hostname.includes("fonts.g");
  if (isStatic) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => save(req, res))));
    return;
  }
  if (url.origin !== location.origin) return;
  if (req.mode === "navigate" && url.pathname === "/") {
    e.respondWith(caches.match("/").then(hit => {
      const net = fetch(req).then(res => res.headers.get("x-tr-app") ? save("/", res) : res);
      if (!hit) return net;
      const slow = new Promise(r => setTimeout(() => r(hit), 3000));
      return Promise.race([net.catch(() => hit), slow]);
    }));
    return;
  }
  const key = url.pathname.startsWith("/data/") ? new Request(url.origin + url.pathname) : req;   // ignore the ?t= cache-buster
  e.respondWith(fetch(req).then(res => save(key, res)).catch(() => caches.match(key).then(hit => hit || caches.match("/"))));
});
