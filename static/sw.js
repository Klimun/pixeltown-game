// PixelTown — Basit Service Worker
// Bu oyun gerçek zamanlı (WebSocket) çalıştığı için agresif cache yapılmaz.
// Sadece PWA/TWA gereksinimini karşılar ve statik ikonları cache'ler.

const CACHE_NAME = "pixeltown-v1";
const STATIC_ASSETS = [
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Network-first: oyun her zaman canlı sunucudan gelsin,
// sadece statik ikonlar için cache'e düş.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (STATIC_ASSETS.some((a) => url.pathname === a)) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
    return;
  }

  // Diğer her şey için ağı dene, başarısız olursa hiçbir şey yapma
  // (WebSocket bu fetch handler'dan geçmez)
});
