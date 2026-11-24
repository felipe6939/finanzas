// Service worker: guarda todo el sitio en el dispositivo para abrir sin internet.
// construir.py reemplaza __VERSION__ (huella del contenido) y __ARCHIVOS__ (lista completa).
const VERSION = "__VERSION__";
const CACHE = "finanzas-" + VERSION;
const ARCHIVOS = __ARCHIVOS__;

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(ARCHIVOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k.startsWith("finanzas-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;  // GitHub API, etc.: directo
  // Caché primero (los archivos de una versión no cambian); si falta, red.
  ev.respondWith(caches.open(CACHE).then((c) => c.match(req, { ignoreSearch: true }).then((r) => r || fetch(req))));
});
