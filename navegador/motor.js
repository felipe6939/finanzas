// Motor de la versión navegador: carga Python (Pyodide) y la app `finanzas` tal cual,
// y entrega cada clic y cada formulario a FastAPI sin servidor (finanzas/web/puente.py).
// La página se muestra en un <iframe> del mismo origen; aquí se interceptan sus enlaces
// y formularios. La URL interna de la app va en el #hash (atrás/adelante y recargar funcionan).

const BASE = new URL("./", location.href);
const $ = (id) => document.getElementById(id);
const vista = $("vista");

let py = null;          // Pyodide
let peticionPy = null;  // finanzas.web.puente.peticion
let urlActual = "/";
let ocupado = false;

function paso(texto) { $("paso").textContent = texto; }

function fallo(titulo, err) {
  console.error(titulo, err);
  $("carga").style.display = "grid";
  vista.style.display = "none";
  $("paso").textContent = titulo;
  const f = $("fallo");
  f.style.display = "block";
  f.textContent = String(err && err.message ? err.message : err).slice(-1500);
}

async function bajar(ruta) {
  const r = await fetch(new URL(ruta, BASE));
  if (!r.ok) throw new Error(`No se pudo descargar ${ruta} (${r.status})`);
  return r.arrayBuffer();
}

async function arrancar() {
  try {
    paso("Cargando Python…");
    const manifiesto = JSON.parse(new TextDecoder().decode(await bajar("app/manifiesto.json")));
    const { loadPyodide } = await import(new URL("pyodide/pyodide.mjs", BASE).href);
    py = await loadPyodide({ indexURL: new URL("pyodide/", BASE).href });

    paso("Cargando librerías…");
    await py.loadPackage(manifiesto.paquetes, { messageCallback: () => {} });
    const sitePackages = py.runPython("import sysconfig; sysconfig.get_paths()['purelib']");
    for (const rueda of manifiesto.ruedas) {
      py.unpackArchive(await bajar(rueda), "wheel", { extractDir: sitePackages });
    }

    paso("Cargando la app…");
    py.unpackArchive(await bajar("app/finanzas.zip"), "zip", { extractDir: "/home/pyodide/app" });
    py.globals.set("PREFIJO_STATIC", new URL("static/", BASE).href);
    py.runPython(`
import os, sys
os.environ["FINANZAS_MODO"] = "navegador"
os.environ["FINANZAS_DATOS"] = "/datos"
os.environ["FINANZAS_INFORMES"] = "/tmp/informes"
sys.path.insert(0, "/home/pyodide/app")
from finanzas.web import puente
puente.iniciar(PREFIJO_STATIC)
`);
    peticionPy = py.pyimport("finanzas.web.puente").peticion;

    $("carga").style.display = "none";
    vista.style.display = "block";
    await navegar("GET", desdeHash() || "/", { historial: "reemplazar" });
    registrarServiceWorker();
  } catch (err) {
    fallo("No se pudo abrir la app.", err);
  }
}

// ------------------------------------------------------------------ peticiones

function desdeHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  return h.startsWith("/") && !h.startsWith("//") ? h : null;
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
  if (ocupado) return;
  ocupado = true;
  document.body.classList.add("ocupada");
  $("ocupado").classList.add("activo");
  try {
    const r = pasarAJs(await peticionPy(metodo, url, py.toPy(cabeceras), cuerpo ? py.toPy(cuerpo) : null));
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
    alertaEnVista("Ocurrió un error inesperado. No se guardó nada de esta acción.\n\n" + (err.message || err).slice(-600));
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

function alertaEnVista(texto) {
  const doc = vista.contentDocument;
  if (!doc || !doc.body) { fallo("Error", texto); return; }
  const div = doc.createElement("div");
  div.className = "banda error";
  div.innerHTML = '<span class="icono">!</span><div></div>';
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
  let accion = (boton && boton.getAttribute("formaction")) || form.getAttribute("action") || urlActual.split("#")[0];
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

// ------------------------------------------------------------------ sin internet

function registrarServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register(new URL("sw.js", BASE)).catch((e) => console.warn("Service worker:", e));
}

// Para las fases siguientes (clave, cifrado, sincronización) y para depurar.
window.finanzas = {
  navegar,
  get py() { return py; },
  get urlActual() { return urlActual; },
};

arrancar();
