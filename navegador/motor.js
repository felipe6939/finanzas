// Motor de la versión navegador: carga Python (Pyodide) y la app `finanzas` tal cual,
// y entrega cada clic y cada formulario a FastAPI sin servidor (finanzas/web/puente.py).
// La página se muestra en un <iframe> del mismo origen; aquí se interceptan sus enlaces
// y formularios. La URL interna de la app va en el #hash (atrás/adelante y recargar funcionan).
//
// Datos: la base SQLite vive en la memoria de Pyodide (/datos/finanzas.db). Tras cada cambio
// se cifra con la clave (boveda.js) y se guarda en el dispositivo (IndexedDB).

import * as boveda from "./boveda.js";

const BASE = new URL("./", location.href);
const $ = (id) => document.getElementById(id);
const vista = $("vista");
const RUTA_DB = "/datos/finanzas.db";
const BLOQUEO_MS = 10 * 60 * 1000;

let py = null;          // Pyodide
let peticionPy = null;  // finanzas.web.puente.peticion
let urlActual = "/";
let ocupado = false;
let sesion = null;      // { llave, sal } mientras la app está abierta; nunca se guarda

function paso(texto) { $("paso").textContent = texto; }

function fallo(titulo, err) {
  console.error(titulo, err);
  vista.style.display = "none";
  $("puerta").hidden = false;
  $("boton-clave").disabled = true;
  paso(titulo);
  $("error-clave").textContent = String(err && err.message ? err.message : err).slice(-1500);
}

async function bajar(ruta) {
  const r = await fetch(new URL(ruta, BASE));
  if (!r.ok) throw new Error(`No se pudo descargar ${ruta} (${r.status})`);
  return r.arrayBuffer();
}

// ------------------------------------------------------------------ arranque

async function prepararPython() {
  paso("Cargando Python…");
  const manifiesto = JSON.parse(new TextDecoder().decode(await bajar("app/manifiesto.json")));
  const { loadPyodide } = await import(new URL("pyodide/pyodide.mjs", BASE).href);
  py = await loadPyodide({ indexURL: new URL("pyodide/", BASE).href });
  paso("Cargando librerías…");
  await py.loadPackage(manifiesto.paquetes, { messageCallback: () => {} });
  const sitePackages = py.runPython("import sysconfig; sysconfig.get_paths()['purelib']");
  for (const rueda of manifiesto.ruedas) py.unpackArchive(await bajar(rueda), "wheel", { extractDir: sitePackages });
  paso("Cargando la app…");
  py.unpackArchive(await bajar("app/finanzas.zip"), "zip", { extractDir: "/home/pyodide/app" });
  py.runPython(`
import os, sys
os.environ["FINANZAS_MODO"] = "navegador"
os.environ["FINANZAS_DATOS"] = "/datos"
os.environ["FINANZAS_INFORMES"] = "/tmp/informes"
os.makedirs("/datos", exist_ok=True)
sys.path.insert(0, "/home/pyodide/app")
`);
  paso("Listo.");
}

async function arrancar() {
  const python = prepararPython();
  python.catch(() => {});  // el error se muestra al esperar la promesa
  let guardado;
  try {
    guardado = await boveda.local.leer("base");
  } catch (err) {
    return fallo("Este navegador no permite guardar datos (¿modo privado?).", err);
  }
  const crear = !guardado;
  $("modo-crear").hidden = !crear;
  $("modo-abrir").hidden = crear;
  $("boton-clave").textContent = crear ? "Crear clave y empezar" : "Abrir";
  $("boton-clave").disabled = false;
  (crear ? $("clave-nueva") : $("clave")).focus();

  $("form-clave").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const error = $("error-clave");
    error.textContent = "";
    const boton = $("boton-clave");
    try {
      let bytes = null;
      if (crear) {
        const clave = $("clave-nueva").value;
        if (clave.length < 8) throw new Error("La clave debe tener al menos 8 caracteres.");
        if (clave !== $("clave-repetir").value) throw new Error("Las dos claves no coinciden.");
        boton.disabled = true; boton.textContent = "Preparando…";
        const sal = boveda.salNueva();
        sesion = { llave: await boveda.derivarLlave(clave, sal), sal };
      } else {
        boton.disabled = true; boton.textContent = "Abriendo…";
        const { sal } = boveda.leerCabecera(guardado.sobre);
        const llave = await boveda.derivarLlave($("clave").value, sal);
        bytes = await boveda.descifrar(llave, guardado.sobre);
        sesion = { llave, sal };
      }
      boton.textContent = "Cargando…";
      await python;
      await abrirApp(bytes);
    } catch (err) {
      sesion = null;
      boton.disabled = false;
      boton.textContent = crear ? "Crear clave y empezar" : "Abrir";
      if (err instanceof boveda.ClaveIncorrecta) {
        error.textContent = "Clave incorrecta.";
        $("clave").select();
      } else if (err && err.name === "PythonError") {
        fallo("No se pudo abrir la app.", err);
      } else {
        error.textContent = err.message || String(err);
      }
    }
  });
}

async function abrirApp(bytes) {
  if (bytes) py.FS.writeFile(RUTA_DB, bytes);
  py.globals.set("PREFIJO_STATIC", new URL("static/", BASE).href);
  py.runPython("from finanzas.web import puente\npuente.iniciar(PREFIJO_STATIC)");  // migra si hace falta
  peticionPy = py.pyimport("finanzas.web.puente").peticion;
  await guardarBase();  // la primera vez (o tras una migración) queda guardada de inmediato
  boveda.pedirPersistencia();
  $("puerta").hidden = true;
  vista.style.display = "block";
  await navegar("GET", sinAvisos(desdeHash()) || "/", { historial: "reemplazar" });
  registrarServiceWorker();
}

// ------------------------------------------------------------------ guardar cifrado

let colaGuardado = Promise.resolve();
let guardadosPendientes = 0;

function guardarBase() {
  guardadosPendientes++;
  colaGuardado = colaGuardado.then(async () => {
    try {
      const bytes = py.FS.readFile(RUTA_DB);
      const sobre = await boveda.cifrar(sesion.llave, sesion.sal, bytes);
      await boveda.local.guardar("base", { sobre, guardado: new Date().toISOString(), bytes: bytes.length });
    } catch (err) {
      console.error("No se pudo guardar", err);
      alertaEnVista("¡No se pudo guardar en este dispositivo! No cierres la app y revisa el espacio disponible.\n\n" + (err.message || err));
    } finally {
      guardadosPendientes--;
    }
  });
  return colaGuardado;
}

window.addEventListener("beforeunload", (ev) => {
  if (guardadosPendientes > 0) { ev.preventDefault(); ev.returnValue = ""; }
});

// Bloqueo automático: en segundo plano más de 10 minutos → se pide la clave otra vez.
let ocultoDesde = null;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { ocultoDesde = Date.now(); return; }
  if (sesion && ocultoDesde && Date.now() - ocultoDesde > BLOQUEO_MS) bloquear();
  ocultoDesde = null;
});

async function bloquear() {
  await colaGuardado;
  sesion = null;
  location.reload();  // borra de la memoria la base descifrada y la llave
}

// ------------------------------------------------------------------ peticiones

function desdeHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  return h.startsWith("/") && !h.startsWith("//") ? h : null;
}

/** La URL guardada sin los avisos de un solo uso (?ok=, ?error=), para no repetirlos al reabrir. */
function sinAvisos(url) {
  if (!url) return url;
  const u = new URL(url, "https://x");
  u.searchParams.delete("ok");
  u.searchParams.delete("error");
  return u.pathname + u.search + u.hash;
}

function pasarAJs(proxy) {
  const estado = proxy.get("estado");
  const url = proxy.get("url");
  const tipo = proxy.get("tipo");
  const texto = proxy.get("texto");
  const modifico = proxy.get("modifico");
  const cab = proxy.get("cabeceras");
  const cabeceras = cab.toJs({ dict_converter: Object.fromEntries });
  cab.destroy();
  let cuerpo = null;
  if (texto == null) {
    const b = proxy.get("cuerpo");
    const buf = b.getBuffer("u8");
    cuerpo = buf.data.slice();
    buf.release();
    b.destroy();
  }
  proxy.destroy();
  return { estado, url, tipo, texto, modifico, cabeceras, cuerpo };
}

async function navegar(metodo, url, { cabeceras = [], cuerpo = null, historial = "agregar" } = {}) {
  if (ocupado || !sesion) return;
  if (url.split("?")[0] === "/seguridad") { abrirPanel(); return; }
  ocupado = true;
  document.body.classList.add("ocupada");
  $("ocupado").classList.add("activo");
  try {
    const r = pasarAJs(await peticionPy(metodo, url, py.toPy(cabeceras), cuerpo ? py.toPy(cuerpo) : null));
    if (r.modifico) await guardarBase();
    const adjunto = /attachment/i.test(r.cabeceras["content-disposition"] || "");
    if (r.texto == null || adjunto) {
      descargar(r);
      return;
    }
    // Tras un POST sin redirección (p. ej. formulario con errores) la URL no se guarda en el historial.
    const guardarUrl = metodo === "GET" || r.url !== url;
    if (guardarUrl) {
      urlActual = r.url;
      const hash = "#" + r.url;
      if (historial === "agregar") history.pushState({ url: r.url }, "", hash);
      else if (historial === "reemplazar") history.replaceState({ url: r.url }, "", hash);
    }
    await mostrar(r.texto, r.url);
    document.dispatchEvent(new CustomEvent("finanzas:respuesta", { detail: { metodo, url: r.url, modifico: r.modifico } }));
  } catch (err) {
    console.error(err);
    alertaEnVista("Ocurrió un error inesperado. No se guardó nada de esta acción.\n\n" + String(err.message || err).slice(-600));
  } finally {
    ocupado = false;
    document.body.classList.remove("ocupada");
    $("ocupado").classList.remove("activo");
  }
}

function descargar(r) {
  const disp = r.cabeceras["content-disposition"] || "";
  const nombre = (disp.match(/filename="?([^";]+)"?/) || [])[1] || "archivo";
  const blob = new Blob([r.cuerpo ?? new TextEncoder().encode(r.texto || "")], { type: r.tipo || "application/octet-stream" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

function alertaEnVista(texto, tipo = "error") {
  const doc = vista.contentDocument;
  if (!doc || !doc.body) { fallo("Error", texto); return; }
  const div = doc.createElement("div");
  div.className = "banda " + tipo;
  div.innerHTML = `<span class="icono">${tipo === "ok" ? "✓" : "!"}</span><div></div>`;
  div.lastChild.textContent = texto;
  div.style.whiteSpace = "pre-wrap";
  (doc.querySelector("main") || doc.body).prepend(div);
  div.scrollIntoView({ block: "start" });
}

// ------------------------------------------------------------------ la página (iframe)

function mostrar(html, url) {
  return new Promise((resolver) => {
    vista.addEventListener("load", () => { prepararPagina(url); resolver(); }, { once: true });
    vista.srcdoc = html;
  });
}

function prepararPagina(url) {
  const win = vista.contentWindow;
  const doc = vista.contentDocument;
  document.title = doc.title || "Finanzas personales";
  win.finanzasIr = (destino) => navegar("GET", destino);
  // form.submit() no dispara el evento «submit» (lo usan los filtros de Movimientos).
  win.HTMLFormElement.prototype.submit = function () { enviarFormulario(this, null); };
  doc.addEventListener("click", alHacerClic);
  doc.addEventListener("submit", alEnviar);
  const frag = url.includes("#") ? decodeURIComponent(url.split("#")[1]) : null;
  if (frag) doc.getElementById(frag)?.scrollIntoView({ block: "center" });
}

function alHacerClic(ev) {
  if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const a = ev.target.closest && ev.target.closest("a[href]");
  if (!a || a.hasAttribute("download")) return;
  const href = a.getAttribute("href");
  if (href.startsWith("#")) {  // ancla dentro de la misma página
    ev.preventDefault();
    if (href.length > 1) vista.contentDocument.getElementById(decodeURIComponent(href.slice(1)))?.scrollIntoView();
    return;
  }
  ev.preventDefault();
  if (href.startsWith("/") && !href.startsWith("//")) navegar("GET", href);
  else window.open(a.href, "_blank", "noopener");  // fuera de la app: pestaña nueva
}

function alEnviar(ev) {
  if (ev.defaultPrevented) return;  // p. ej. onsubmit="return confirm(...)" respondido con «Cancelar»
  const form = ev.target;
  if ((form.getAttribute("method") || "").toLowerCase() === "dialog") return;
  ev.preventDefault();
  enviarFormulario(form, ev.submitter || null);
}

async function enviarFormulario(form, boton) {
  const metodo = ((boton && boton.getAttribute("formmethod")) || form.getAttribute("method") || "GET").toUpperCase();
  const accion = (boton && boton.getAttribute("formaction")) || form.getAttribute("action") || urlActual.split("#")[0];
  let datos;
  try { datos = new FormData(form, boton || undefined); } catch { datos = new FormData(form); }
  if (boton && boton.name && !datos.has(boton.name)) datos.append(boton.name, boton.value);

  if (metodo === "GET") {
    const params = new URLSearchParams();
    for (const [k, v] of datos) if (typeof v === "string") params.append(k, v);
    const qs = params.toString();
    return navegar("GET", accion.split("?")[0] + (qs ? "?" + qs : ""));
  }
  const enctype = ((boton && boton.getAttribute("formenctype")) || form.getAttribute("enctype") || "").toLowerCase();
  let tipo, cuerpo;
  if (enctype === "multipart/form-data") {
    const req = new Request(location.href, { method: "POST", body: datos });
    tipo = req.headers.get("content-type");
    cuerpo = new Uint8Array(await req.arrayBuffer());
  } else {
    const params = new URLSearchParams();
    for (const [k, v] of datos) if (typeof v === "string") params.append(k, v);
    tipo = "application/x-www-form-urlencoded";
    cuerpo = new TextEncoder().encode(params.toString());
  }
  return navegar("POST", accion, { cabeceras: [["content-type", tipo]], cuerpo });
}

window.addEventListener("popstate", (ev) => {
  const url = (ev.state && ev.state.url) || desdeHash() || "/";
  if (peticionPy && url !== urlActual) navegar("GET", url, { historial: "ninguno" });
});

// ------------------------------------------------------------------ panel «Seguridad»

const fechaHora = (iso) => new Date(iso).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" });

async function abrirPanel() {
  const g = await boveda.local.leer("base");
  $("panel-guardado").textContent = g ? fechaHora(g.guardado) : "—";
  let persistente = false;
  try { persistente = navigator.storage && navigator.storage.persisted ? await navigator.storage.persisted() : false; } catch {}
  $("panel-persistente").textContent = persistente ? "activa" : "no garantizada";
  $("cambio-error").textContent = "";
  $("form-cambio").reset();
  $("panel").showModal();
}

$("boton-cerrar-panel").addEventListener("click", () => $("panel").close());
$("boton-bloquear").addEventListener("click", () => bloquear());

$("form-cambio").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const error = $("cambio-error");
  error.textContent = "";
  const nueva = $("cambio-nueva").value;
  if (nueva.length < 8) { error.textContent = "La clave nueva debe tener al menos 8 caracteres."; return; }
  if (nueva !== $("cambio-repetir").value) { error.textContent = "Las dos claves nuevas no coinciden."; return; }
  const boton = ev.target.querySelector("button[type=submit]");
  boton.disabled = true;
  try {
    // Se comprueba la clave actual descifrando la copia guardada.
    const g = await boveda.local.leer("base");
    const llaveActual = await boveda.derivarLlave($("cambio-actual").value, boveda.leerCabecera(g.sobre).sal);
    await boveda.descifrar(llaveActual, g.sobre);
    await colaGuardado;
    const sal = boveda.salNueva();
    sesion = { llave: await boveda.derivarLlave(nueva, sal), sal };
    await guardarBase();
    $("panel").close();
    alertaEnVista("Clave cambiada. Desde ahora, usa la clave nueva.", "ok");
  } catch (err) {
    error.textContent = err instanceof boveda.ClaveIncorrecta ? "La clave actual no es correcta." : (err.message || String(err));
  } finally {
    boton.disabled = false;
  }
});

// ------------------------------------------------------------------ sin internet

function registrarServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register(new URL("sw.js", BASE)).catch((e) => console.warn("Service worker:", e));
}

// Para las fases siguientes (sincronización) y para depurar.
window.finanzas = {
  navegar,
  guardarBase,
  get py() { return py; },
  get urlActual() { return urlActual; },
  get abierta() { return !!sesion; },
};

arrancar();
