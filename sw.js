// Network first, so data is always fresh; cache is only used when offline.
const C = "sector-stock-edge-v2";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(r => {
      caches.open(C).then(c => c.put(e.request, r.clone()));
      return r;
    }).catch(() => caches.match(e.request))
  );
});
