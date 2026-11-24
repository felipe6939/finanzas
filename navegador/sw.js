// Service worker: guarda todo el sitio en el dispositivo para abrir sin internet.
// construir.py reemplaza __VERSION__ (huella del contenido) y __ARCHIVOS__ (lista completa).
//
// Estrategia:
// - Python y librerías (pyodide/, wheels/): nombres con versión, nunca cambian → caché primero.
// - Todo lo demás (página, motor, app, estilos): RED primero, con la copia guardada como respaldo
//   sin internet. Así cada publicación nueva llega al abrir la app, sin depender de que el
//   navegador termine de actualizar el service worker (en el iPhone eso podía no pasar nunca).
const VERSION = "__VERSION__";
const CACHE = "finanzas-" + VERSION;
const ARCHIVOS = __ARCHIVOS__;
const INMUTABLE = /\/(pyodide|wheels)\//;
const ESPERA_RED_MS = 6000;

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(ARCHIVOS.map((u) => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k.startsWith("finanzas-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function redPrimero(req) {
  const cache = await caches.open(CACHE);
  try {
    const resp = await fetch(req, { cache: "no-cache", signal: AbortSignal.timeout(ESPERA_RED_MS) });
    if (resp.ok) {
      const url = new URL(req.url);
      url.search = "";
      await cache.put(url.href, resp.clone());
    }
    return resp;
  } catch (err) {
    const guardada = await cache.match(req, { ignoreSearch: true });
    if (guardada) return guardada;
    throw err;
  }
}

async function cachePrimero(req) {
  const cache = await caches.open(CACHE);
  return (await cache.match(req, { ignoreSearch: true })) || fetch(req);
}

self.addEventListener("fetch", (ev) => {
  const req = ev.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;  // GitHub API, etc.: directo
  ev.respondWith(INMUTABLE.test(new URL(req.url).pathname) ? cachePrimero(req) : redPrimero(req));
});
