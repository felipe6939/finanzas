// Motor de la versión navegador: carga Python (Pyodide) y la app `finanzas` tal cual,
// y entrega cada clic y cada formulario a FastAPI sin servidor (finanzas/web/puente.py).
// La página se muestra en un <iframe> del mismo origen; aquí se interceptan sus enlaces
// y formularios. La URL interna de la app va en el #hash (atrás/adelante y recargar funcionan).
//
// Datos: la base SQLite vive en la memoria de Pyodide (/datos/finanzas.db). Tras cada cambio
// se cifra con la clave (boveda.js), se guarda en el dispositivo (IndexedDB) y, si está
// conectada, se sube a la nube: repositorio privado de GitHub (nube.js).

import * as boveda from "./boveda.js";
import * as nube from "./nube.js";

const BASE = new URL("./", location.href);
const $ = (id) => document.getElementById(id);
const vista = $("vista");
const RUTA_DB = "/datos/finanzas.db";
const BLOQUEO_MS = 10 * 60 * 1000;
const DIAS_RECORDAR_COPIA = 7;

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
  const TEXTOS = { crear: "Crear clave y empezar", abrir: "Abrir", recuperar: "Recuperar y abrir", archivo: "Abrir copia" };
  let modo;
  const ponerModo = (m) => {
    modo = m;
    for (const k of Object.keys(TEXTOS)) $("modo-" + k).hidden = k !== m;
    $("boton-clave").textContent = TEXTOS[m];
    $("error-clave").textContent = "";
    ({ crear: $("clave-nueva"), abrir: $("clave"), recuperar: $("rec-token"), archivo: $("arch-archivo") })[m].focus();
  };
  $("rec-repo").value = nube.REPO_PREDETERMINADO;
  $("ir-recuperar").addEventListener("click", () => ponerModo("recuperar"));
  $("ir-crear").addEventListener("click", () => ponerModo("crear"));
  $("ir-archivo").addEventListener("click", () => ponerModo("archivo"));
  $("ir-crear-2").addEventListener("click", () => ponerModo("crear"));
  $("boton-clave").disabled = false;
  ponerModo(guardado ? "abrir" : "crear");

  $("form-clave").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const error = $("error-clave");
    error.textContent = "";
    const boton = $("boton-clave");
    try {
      let bytes = null;
      if (modo === "crear") {
        const clave = $("clave-nueva").value;
        if (clave.length < 8) throw new Error("La clave debe tener al menos 8 caracteres.");
        if (clave !== $("clave-repetir").value) throw new Error("Las dos claves no coinciden.");
        boton.disabled = true; boton.textContent = "Preparando…";
        const sal = boveda.salNueva();
        sesion = { llave: await boveda.derivarLlave(clave, sal), sal };
        await nube.cargar(sesion.llave);
      } else if (modo === "archivo") {
        const archivo = $("arch-archivo").files[0];
        if (!archivo) throw new Error("Elige el archivo .cifrado.");
        boton.disabled = true; boton.textContent = "Abriendo…";
        const sobre = new Uint8Array(await archivo.arrayBuffer());
        const { sal } = boveda.leerCabecera(sobre);
        const llave = await boveda.derivarLlave($("arch-clave").value, sal);
        bytes = await boveda.descifrar(llave, sobre);
        sesion = { llave, sal };
        await nube.cargar(llave);
      } else if (modo === "recuperar") {
        boton.disabled = true; boton.textContent = "Descargando…";
        const r = await nube.recuperar($("rec-repo").value, $("rec-token").value, $("rec-clave").value);
        sesion = { llave: r.llave, sal: r.sal };
        bytes = r.bytes;
      } else {
        boton.disabled = true; boton.textContent = "Abriendo…";
        const clave = $("clave").value;
        const { sal } = boveda.leerCabecera(guardado.sobre);
        const llave = await boveda.derivarLlave(clave, sal);
        bytes = await boveda.descifrar(llave, guardado.sobre);
        sesion = { llave, sal };
        if (await nube.cargar(llave)) {
          boton.textContent = "Revisando la nube…";
          const r = await nube.alAbrir(clave, llave, sal, guardado);
          if (r) { bytes = r.bytes; sesion = { llave: r.llave, sal: r.sal }; }
        }
      }
      boton.textContent = "Cargando…";
      await python;
      await abrirApp(bytes, modo === "archivo");  // una copia abierta desde archivo se guarda de una vez
    } catch (err) {
      sesion = null;
      boton.disabled = false;
      boton.textContent = TEXTOS[modo];
      if (err instanceof boveda.ClaveIncorrecta) {
        error.textContent = "Clave incorrecta.";
        ({ recuperar: $("rec-clave"), archivo: $("arch-clave") }[modo] || $("clave")).select();
      } else if (err && err.name === "PythonError") {
        fallo("No se pudo abrir la app.", err);
      } else {
        error.textContent = err.message || String(err);
      }
    }
  });
}

async function abrirApp(bytes, guardarYa = false) {
  if (bytes) py.FS.writeFile(RUTA_DB, bytes);
  py.globals.set("PREFIJO_STATIC", new URL("static/", BASE).href);
  py.runPython("from finanzas.web import puente\npuente.iniciar(PREFIJO_STATIC)");  // migra si hace falta
  peticionPy = py.pyimport("finanzas.web.puente").peticion;
  // La primera vez (o tras una migración) queda guardada de inmediato; si no cambió nada, no.
  if (bytes && !guardarYa && mismosBytes(bytes, py.FS.readFile(RUTA_DB))) ultimosBytes = bytes;
  else await guardarBase();
  const rev = revisar();
  if (rev.integridad !== "ok") {
    alertaPendiente = "¡Tu base de datos no pasó la revisión de integridad! No registres nada y restaura una copia " +
      "(Seguridad → Versiones en la nube o Restaurar desde un archivo).\n\n" + rev.integridad;
  } else {
    const base = await boveda.local.leer("base");
    if (base && base.registros == null) await boveda.local.guardar("base", { ...base, registros: rev.registros });
    if (!(await nube.hayPendiente())) await nube.anotarRegistros(rev.registros);
    await recordarCopia();
  }
  boveda.pedirPersistencia();
  $("puerta").hidden = true;
  vista.style.display = "block";
  document.body.classList.add("abierta");
  nube.subirAhora();  // cambios que quedaron sin subir (p. ej. sin internet)
  await navegar("GET", sinAvisos(desdeHash()) || "/", { historial: "reemplazar" });
  if (alertaPendiente) { alertaEnVista(alertaPendiente, alertaPendiente.startsWith("¡") ? "error" : "aviso"); alertaPendiente = null; }
  registrarServiceWorker();
}

let alertaPendiente = null;

/** Integridad y número de registros de la base (finanzas.web.puente.revisar_base). */
function revisar(ruta = RUTA_DB) {
  const r = py.pyimport("finanzas.web.puente").revisar_base(ruta);
  const salida = { integridad: r.get("integridad"), registros: r.get("registros") };
  r.destroy();
  return salida;
}

async function recordarCopia() {
  let ultima = await boveda.local.leer("ultima-copia");
  if (!ultima) { ultima = new Date().toISOString(); await boveda.local.guardar("ultima-copia", ultima); }  // primer uso
  const dias = Math.floor((Date.now() - new Date(ultima)) / 86400000);
  if (dias > DIAS_RECORDAR_COPIA) {
    alertaPendiente = `Hace ${dias} días que no descargas una copia de tus datos. Ve a Seguridad → «Descargar copia cifrada» y guárdala en tu PC o en Archivos.`;
  }
}

function mismosBytes(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// ------------------------------------------------------------------ guardar cifrado

let colaGuardado = Promise.resolve();
let guardadosPendientes = 0;
let ultimosBytes = null;  // lo último guardado: si nada cambió, no se guarda ni se sube

/** Cifra y guarda la base en el dispositivo (y programa la subida). `forzar`: aunque no haya cambiado. */
function guardarBase(forzar = false) {
  guardadosPendientes++;
  colaGuardado = colaGuardado.then(async () => {
    try {
      const bytes = py.FS.readFile(RUTA_DB);
      if (!forzar && ultimosBytes && mismosBytes(bytes, ultimosBytes)) return;
      const rev = revisar();
      if (rev.integridad !== "ok") {  // nunca guardar (ni subir) una base dañada
        alertaEnVista("¡La base no pasó la revisión de integridad y NO se guardó este cambio! Cierra la app sin registrar más " +
                      "y vuelve a abrirla: se usará la última copia buena.\n\n" + rev.integridad);
        return;
      }
      const sobre = await boveda.cifrar(sesion.llave, sesion.sal, bytes);
      await boveda.local.guardar("base", { sobre, guardado: new Date().toISOString(), bytes: bytes.length, registros: rev.registros });
      ultimosBytes = bytes;
      nube.programarSubida();
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
  await Promise.race([nube.subirAhora(), new Promise((ok) => setTimeout(ok, 5000))]);
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
      if (url.startsWith("/configuracion/exportar")) await boveda.local.guardar("ultima-copia", new Date().toISOString());
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
  $("nube-error").textContent = "";
  $("nube-error-2").textContent = "";
  $("nube-token").value = "";
  if (!$("nube-repo").value) $("nube-repo").value = nube.REPO_PREDETERMINADO;
  pintarNube(nube.estadoActual());
  const ultima = await boveda.local.leer("ultima-copia");
  $("copia-fecha").textContent = ultima ? fechaHora(ultima) : "nunca";
  $("copias-error").textContent = "";
  $("versiones").hidden = true;
  $("restaurar-archivo").value = "";
  $("restaurar-clave").value = "";
  if (!$("panel").open) $("panel").showModal();
}

// ------------------------------------------------------------------ nube: indicador y panel

const TEXTO_FASE = {
  apagado: "Solo en este dispositivo", ok: "Sincronizado", subiendo: "Subiendo…",
  pendiente: "Pendiente de subir", error: "Error de la nube",
};

function textoEstado(e) {
  if (e.fase === "problema") return { clave: "La nube usa otra clave", freno: "Freno: revisa antes de subir" }[e.problema] || "Conflicto: elige una copia";
  return TEXTO_FASE[e.fase] || e.fase;
}

function pintarNube(e) {
  const ind = $("indicador");
  ind.dataset.fase = e.fase;
  $("indicador-texto").textContent = textoEstado(e);
  ind.title = e.detalle || "Copia en la nube";
  const conectada = nube.configurada();
  $("nube-apagada").hidden = conectada;
  $("boton-versiones").hidden = !conectada;
  $("nube-encendida").hidden = !conectada;
  if (!conectada) return;
  $("nube-repo-texto").textContent = nube.repo();
  $("nube-estado").textContent = textoEstado(e) + (e.detalle && e.fase !== "ok" ? " — " + e.detalle : "");
  $("nube-fecha").textContent = e.sincronizado ? fechaHora(e.sincronizado) : "—";
  $("nube-conflicto").hidden = e.problema !== "conflicto";
  $("nube-clave").hidden = e.problema !== "clave";
  $("nube-freno").hidden = e.problema !== "freno";
  if (e.freno) { $("freno-aqui").textContent = e.freno.aqui; $("freno-nube").textContent = e.freno.nube; }
  $("nube-resolver").hidden = !e.problema;
}

nube.alCambiar(pintarNube);
$("indicador").addEventListener("click", () => abrirPanel());

/** Acciones delicadas: el primer toque pide confirmación, el segundo (antes de 6 s) ejecuta. */
function confirmado(boton) {
  if (boton.dataset.confirmar) { delete boton.dataset.confirmar; boton.textContent = boton.dataset.texto; return true; }
  boton.dataset.texto = boton.textContent;
  boton.dataset.confirmar = "1";
  boton.textContent = "¿Seguro? Toca otra vez";
  setTimeout(() => { if (boton.dataset.confirmar) { delete boton.dataset.confirmar; boton.textContent = boton.dataset.texto; } }, 6000);
  return false;
}

async function conBoton(boton, salida, fn) {
  salida.textContent = "";
  boton.disabled = true;
  try { await fn(); }
  catch (err) { salida.textContent = err instanceof boveda.ClaveIncorrecta ? "Clave incorrecta." : (err.message || String(err)); }
  finally { boton.disabled = false; }
}

$("form-nube").addEventListener("submit", (ev) => {
  ev.preventDefault();
  conBoton(ev.target.querySelector("button[type=submit]"), $("nube-error"), async () => {
    await colaGuardado;
    const r = await nube.conectar(sesion.llave, $("nube-repo").value, $("nube-token").value);
    $("nube-token").value = "";
    if (r === "subido") alertaEnVista("Conectado. Tus datos cifrados ya están en tu repositorio privado.", "ok");
  });
});

$("boton-sincronizar").addEventListener("click", (ev) =>
  conBoton(ev.currentTarget, $("nube-error-2"), async () => { await colaGuardado; await nube.subirAhora(); }));

$("boton-desconectar").addEventListener("click", (ev) => {
  if (!confirmado(ev.currentTarget)) return;
  conBoton(ev.currentTarget, $("nube-error-2"), () => nube.desconectar());
});

$("boton-usar-local").addEventListener("click", (ev) => {
  if (!confirmado(ev.currentTarget)) return;
  conBoton(ev.currentTarget, $("nube-error-2"), async () => {
    await colaGuardado;
    await nube.usarCopiaLocal();
    $("panel").close();
    alertaEnVista("Listo: la nube quedó con la copia de este dispositivo.", "ok");
  });
});

$("boton-usar-nube").addEventListener("click", (ev) => {
  if (!confirmado(ev.currentTarget)) return;
  conBoton(ev.currentTarget, $("nube-error-2"), async () => {
    await colaGuardado;
    const otra = nube.estadoActual().problema === "clave" ? $("nube-clave-otra").value : null;
    await nube.usarCopiaNube(otra, otra ? null : sesion.llave, otra ? null : sesion.sal);
    sesion = null;
    location.reload();  // se abre la copia bajada (pide la clave otra vez)
  });
});

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
    await nube.alCambiarLlave(sesion.llave);
    await guardarBase(true);
    $("panel").close();
    alertaEnVista("Clave cambiada. Desde ahora, usa la clave nueva.", "ok");
  } catch (err) {
    error.textContent = err instanceof boveda.ClaveIncorrecta ? "La clave actual no es correcta." : (err.message || String(err));
  } finally {
    boton.disabled = false;
  }
});

// ------------------------------------------------------------------ copias: descargar y restaurar

$("boton-descargar-copia").addEventListener("click", (ev) =>
  conBoton(ev.currentTarget, $("copias-error"), async () => {
    await colaGuardado;
    const base = await boveda.local.leer("base");
    const hoy = new Date().toLocaleDateString("sv-SE");  // AAAA-MM-DD
    descargar({ cabeceras: { "content-disposition": `attachment; filename="finanzas-${hoy}.cifrado"` },
                cuerpo: base.sobre, tipo: "application/octet-stream" });
    const ahora = new Date().toISOString();
    await boveda.local.guardar("ultima-copia", ahora);
    $("copia-fecha").textContent = fechaHora(ahora);
  }));

/** Descifra un sobre (archivo o versión) con la llave actual o, si se guardó con otra clave, con la escrita. */
async function descifrarCopia(sobre) {
  const { sal } = boveda.leerCabecera(sobre);
  const otra = $("restaurar-clave").value;
  const misma = boveda.b64(sal) === boveda.b64(sesion.sal);
  if (!misma && !otra) throw new Error("Esa copia se guardó con otra clave: escríbela en «Clave de esa copia».");
  return boveda.descifrar(misma ? sesion.llave : await boveda.derivarLlave(otra, sal), sobre);
}

/** Restaura una copia: la revisa, la cifra con la clave actual, guarda aparte la de ahora y reabre la app. */
async function restaurarBytes(bytes) {
  const tmp = "/tmp/restaurar.db";
  py.FS.writeFile(tmp, bytes);
  const rev = revisar(tmp);
  py.FS.unlink(tmp);
  if (rev.integridad !== "ok") throw new Error("Esa copia está dañada y no se restauró: " + rev.integridad);
  await colaGuardado;
  const actual = await boveda.local.leer("base");
  if (actual) await boveda.local.guardar("base-anterior", { ...actual, reemplazado: new Date().toISOString() });
  const sobre = await boveda.cifrar(sesion.llave, sesion.sal, bytes);
  await boveda.local.guardar("base", { sobre, guardado: new Date().toISOString(), bytes: bytes.length, registros: rev.registros });
  await nube.permitirMenos();
  sesion = null;
  location.reload();  // se abre la copia restaurada (pide la clave actual)
}

$("boton-restaurar-archivo").addEventListener("click", (ev) => {
  const archivo = $("restaurar-archivo").files[0];
  if (!archivo) { $("copias-error").textContent = "Elige primero el archivo .cifrado."; return; }
  if (!confirmado(ev.currentTarget)) return;
  conBoton(ev.currentTarget, $("copias-error"), async () =>
    restaurarBytes(await descifrarCopia(new Uint8Array(await archivo.arrayBuffer()))));
});

$("boton-versiones").addEventListener("click", (ev) =>
  conBoton(ev.currentTarget, $("copias-error"), async () => {
    const versiones = await nube.listarVersiones();
    const lista = $("versiones-lista");
    lista.replaceChildren();
    versiones.forEach((v, i) => {
      const fila = document.createElement("tr");
      const desde = (v.mensaje.match(/desde (\S+)/) || [])[1] || "";
      fila.innerHTML = `<td></td><td class="der"></td>`;
      fila.firstChild.textContent = fechaHora(v.fecha) + (desde ? ` · ${desde}` : "") + (i === 0 ? " (actual)" : "");
      if (i > 0) {
        const b = document.createElement("button");
        b.className = "btn"; b.type = "button"; b.textContent = "Restaurar";
        b.addEventListener("click", () => {
          if (!confirmado(b)) return;
          conBoton(b, $("copias-error"), async () => restaurarBytes(await descifrarCopia(await nube.leerVersion(v.sha))));
        });
        fila.lastChild.append(b);
      }
      lista.append(fila);
    });
    if (!versiones.length) lista.innerHTML = `<tr><td class="muted">Todavía no hay versiones.</td></tr>`;
    $("versiones").hidden = false;
  }));

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
  nube,
};

arrancar();
