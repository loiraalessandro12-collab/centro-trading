// Sfida 90: lavora anche senza rete. Prima prova la rete (dati e bozza cambiano spesso), poi l'ultima copia salvata.
const VERSIONE = "sfida90-2";
const GUSCIO = ["./", "index.html", "manifest.webmanifest", "icona-192.png", "icona-512.png", "apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSIONE).then((c) => c.addAll(GUSCIO)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== VERSIONE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) {
      const copia = r.clone();
      caches.open(VERSIONE).then((c) => c.put(e.request, copia));
    }
    return r;
  }).catch(() => caches.match(e.request, {ignoreSearch: true})
    .then((r) => r || (e.request.mode === "navigate" ? caches.match("index.html") : undefined))));
});
