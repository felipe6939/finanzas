// Nube: copia cifrada de la base en un repositorio PRIVADO de GitHub (API «Contents»).
//
// - Se sube el mismo sobre cifrado que se guarda en el dispositivo (boveda.js): GitHub nunca ve
//   los datos, solo bytes cifrados. Cada subida es un commit → historial de versiones.
// - El token de GitHub se guarda cifrado con la llave de la sesión; sin la clave no sirve de nada.
// - Conflictos: cada subida lleva el «sha» de la última versión conocida. Si la nube cambió
//   desde otro dispositivo y aquí también hay cambios sin subir, NO se mezcla nada: se avisa y
//   se detiene la sincronización hasta que el usuario elija qué copia conservar.
// - Sin internet: los cambios quedan en el dispositivo y se suben al volver la conexión
//   (pendiente = la copia local es distinta de la última subida).
//
// IndexedDB: "nube" → {repo, token: {iv, datos}}; "nube-estado" → {sha, subido, problema, sincronizado}
//            "base-anterior" → la copia local que se reemplazó al bajar de la nube (por si acaso).

import * as boveda from "./boveda.js";

const API = "https://api.github.com";
export const RUTA = "finanzas.cifrado";
export const REPO_PREDETERMINADO = "felipe6939/finanzas-datos";
const ESPERA_SUBIDA_MS = 2000;
const REINTENTO_MS = 60 * 1000;

export class ErrorNube extends Error {
  constructor(mensaje, tipo) { super(mensaje); this.tipo = tipo; }  // red | token | permiso | publico | conflicto | datos
}

let cfg = null;        // {repo, token} en memoria mientras la app está abierta
let llaveSesion = null;
let estado = {};
let fase = "apagado";  // apagado | ok | subiendo | pendiente | problema
let detalle = "";
let oyente = () => {};

export const configurada = () => !!cfg;
export const repo = () => cfg && cfg.repo;
export const estadoActual = () => ({ fase, detalle, problema: estado.problema || null, sincronizado: estado.sincronizado || null });
export function alCambiar(fn) { oyente = fn; avisar(); }

function avisar(nuevaFase, nuevoDetalle = "") {
  if (nuevaFase) { fase = nuevaFase; detalle = nuevoDetalle; }
  if (!cfg) fase = "apagado";
  else if (estado.problema) fase = "problema";
  try { oyente(estadoActual()); } catch (e) { console.error(e); }
}

const dispositivo = () => (/iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : "navegador");

// ------------------------------------------------------------------ API de GitHub

async function pedir(token, metodo, ruta, { cuerpo, accept } = {}) {
  let r;
  try {
    r = await fetch(API + ruta, {
      method: metodo,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: accept || "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        ...(cuerpo ? { "Content-Type": "application/json" } : {}),
      },
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
    });
  } catch {
    throw new ErrorNube("Sin conexión con GitHub.", "red");
  }
  if (r.status === 401) throw new ErrorNube("GitHub rechazó el token: no es válido o ya venció. Crea uno nuevo y conéctalo otra vez.", "token");
  if (r.status === 403 || r.status === 429) {
    const limite = r.headers.get("x-ratelimit-remaining") === "0";
    throw new ErrorNube(limite ? "GitHub limitó las peticiones por un rato. Se reintentará."
      : "El token no tiene permiso para escribir en el repositorio (necesita «Contents: Read and write»).", limite ? "red" : "permiso");
  }
  if (r.status >= 500) throw new ErrorNube(`GitHub no responde (error ${r.status}). Se reintentará.`, "red");
  return r;
}

const rutaRepo = (repo) => "/repos/" + repo.split("/").map(encodeURIComponent).join("/");

/** Comprueba que el repositorio exista, sea accesible y sea PRIVADO. */
async function verificarRepo(token, repo) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new ErrorNube("El repositorio debe tener la forma usuario/nombre.", "datos");
  const r = await pedir(token, "GET", rutaRepo(repo));
  if (r.status === 404) throw new ErrorNube("No se encontró el repositorio, o el token no tiene acceso a él.", "permiso");
  if (!r.ok) throw new ErrorNube(`GitHub respondió ${r.status} al revisar el repositorio.`, "red");
  const info = await r.json();
  if (!info.private) throw new ErrorNube("Ese repositorio es PÚBLICO. Por seguridad, tus datos solo se guardan en un repositorio privado.", "publico");
}

/** Lee la copia de la nube: {sha, sobre} o null si todavía no existe. */
async function leerRemoto(token, repo) {
  const ruta = `${rutaRepo(repo)}/contents/${RUTA}`;
  const r = await pedir(token, "GET", ruta);
  if (r.status === 404) return null;
  if (!r.ok) throw new ErrorNube(`GitHub respondió ${r.status} al leer la copia.`, "red");
  const info = await r.json();
  let sobre;
  if (info.encoding === "base64" && info.content) {
    sobre = boveda.deB64(info.content);
  } else {  // archivos de más de 1 MB: se piden en bruto
    const crudo = await pedir(token, "GET", ruta, { accept: "application/vnd.github.raw+json" });
    if (!crudo.ok) throw new ErrorNube(`GitHub respondió ${crudo.status} al leer la copia.`, "red");
    sobre = new Uint8Array(await crudo.arrayBuffer());
  }
  return { sha: info.sha, sobre };
}

/** Sube el sobre. `sha` = versión que se reemplaza (null si el archivo no existe). Devuelve el sha nuevo. */
async function escribirRemoto(token, repo, sobre, sha) {
  const cuerpo = { message: `Actualización desde ${dispositivo()} · ${new Date().toISOString()}`, content: boveda.b64(sobre) };
  if (sha) cuerpo.sha = sha;
  const r = await pedir(token, "PUT", `${rutaRepo(repo)}/contents/${RUTA}`, { cuerpo });
  if (r.status === 409 || r.status === 422) throw new ErrorNube("La copia de la nube cambió desde otro dispositivo.", "conflicto");
  if (r.status === 404) throw new ErrorNube("No se encontró el repositorio, o el token no tiene acceso a él.", "permiso");
  if (!r.ok) throw new ErrorNube(`GitHub respondió ${r.status} al subir la copia.`, "red");
  return (await r.json()).content.sha;
}

// ------------------------------------------------------------------ estado guardado

async function guardarEstado(cambios) {
  estado = { ...estado, ...cambios };
  await boveda.local.guardar("nube-estado", estado);
}

async function guardarConfig(llave, repo, token) {
  await boveda.local.guardar("nube", { repo, token: await boveda.cifrarTexto(llave, token) });
  cfg = { repo, token };
  llaveSesion = llave;
}

/** Al abrir (después de descifrar la copia local): lee la configuración y descifra el token. */
export async function cargar(llave) {
  llaveSesion = llave;
  estado = (await boveda.local.leer("nube-estado")) || {};
  const guardada = await boveda.local.leer("nube");
  cfg = guardada ? { repo: guardada.repo, token: await boveda.descifrarTexto(llave, guardada.token) } : null;
  avisar(cfg ? "ok" : "apagado");
  return !!cfg;
}

/** La clave cambió en este dispositivo: el token se vuelve a cifrar con la llave nueva. */
export async function alCambiarLlave(llave) {
  if (cfg) await guardarConfig(llave, cfg.repo, cfg.token);
  llaveSesion = llave;
}

async function adoptarRemoto(remoto, bytes, llave) {
  const anterior = await boveda.local.leer("base");
  if (anterior) await boveda.local.guardar("base-anterior", { ...anterior, reemplazado: new Date().toISOString() });
  const guardado = new Date().toISOString();
  await boveda.local.guardar("base", { sobre: remoto.sobre, guardado, bytes: bytes.length });
  await guardarEstado({ sha: remoto.sha, subido: guardado, problema: null, sincronizado: guardado });
  if (llave !== llaveSesion) await alCambiarLlave(llave);
}

async function descifrarRemoto(remoto, clave, llaveActual, salActual) {
  const { sal } = boveda.leerCabecera(remoto.sobre);
  const misma = salActual && boveda.b64(sal) === boveda.b64(salActual);
  if (!(misma && llaveActual) && !clave) throw new boveda.ClaveIncorrecta("La copia de la nube usa otra clave.");
  const llave = misma && llaveActual ? llaveActual : await boveda.derivarLlave(clave, sal);
  return { llave, sal, bytes: await boveda.descifrar(llave, remoto.sobre) };
}

/**
 * Al abrir, antes de arrancar la app: si la nube tiene una versión más nueva y aquí no hay
 * cambios sin subir, se baja. Devuelve {bytes, llave, sal} si hay que usar la copia bajada.
 */
export async function alAbrir(clave, llave, sal, baseLocal) {
  if (!cfg || estado.problema) { avisar(); return null; }
  let remoto;
  try {
    remoto = await leerRemoto(cfg.token, cfg.repo);
  } catch (err) {
    avisar(err.tipo === "red" ? "pendiente" : "error", err.message);
    return null;
  }
  if (!remoto || remoto.sha === estado.sha) return null;  // nada nuevo (o aún no hay copia: se sube después)
  if (baseLocal.guardado !== estado.subido) {  // cambios aquí y allá: no se mezcla nada
    await guardarEstado({ problema: "conflicto" });
    avisar();
    return null;
  }
  try {
    const r = await descifrarRemoto(remoto, clave, llave, sal);
    await adoptarRemoto(remoto, r.bytes, r.llave);
    avisar("ok");
    return r;
  } catch (err) {
    if (!(err instanceof boveda.ClaveIncorrecta)) throw err;
    await guardarEstado({ problema: "clave" });
    avisar();
    return null;
  }
}

// ------------------------------------------------------------------ subir

let temporizador = null;
let subiendo = null;
let repetir = false;

/** Tras cada guardado local: sube a la nube unos segundos después (agrupa cambios seguidos). */
export function programarSubida() {
  if (!cfg) return;
  clearTimeout(temporizador);
  temporizador = setTimeout(() => subirAhora(), ESPERA_SUBIDA_MS);
}

export async function subirAhora() {
  clearTimeout(temporizador);
  if (!cfg || estado.problema) { avisar(); return; }
  if (subiendo) { repetir = true; return subiendo; }
  subiendo = (async () => {
    try {
      const base = await boveda.local.leer("base");
      if (!base || base.guardado === estado.subido) { avisar("ok"); return; }
      avisar("subiendo");
      const sha = await escribirRemoto(cfg.token, cfg.repo, base.sobre, estado.sha);
      await guardarEstado({ sha, subido: base.guardado, sincronizado: new Date().toISOString() });
      avisar("ok");
    } catch (err) {
      if (err.tipo === "conflicto") { await guardarEstado({ problema: "conflicto" }); avisar(); }
      else avisar(err.tipo === "red" ? "pendiente" : "error", err.message);
    }
  })();
  try { await subiendo; } finally {
    subiendo = null;
    if (repetir) { repetir = false; subirAhora(); }
  }
}

export const hayPendiente = async () => {
  if (!cfg) return false;
  const base = await boveda.local.leer("base");
  return !!base && base.guardado !== estado.subido;
};

window.addEventListener("online", () => { if (cfg && fase === "pendiente") subirAhora(); });
setInterval(() => { if (cfg && fase === "pendiente" && navigator.onLine !== false) subirAhora(); }, REINTENTO_MS);

// ------------------------------------------------------------------ conectar / recuperar / resolver

/** Conecta este dispositivo (que ya tiene datos) al repositorio. */
export async function conectar(llave, repoNuevo, token) {
  repoNuevo = repoNuevo.trim(); token = token.trim();
  if (!token) throw new ErrorNube("Escribe el token.", "datos");
  await verificarRepo(token, repoNuevo);
  const remoto = await leerRemoto(token, repoNuevo);
  estado = {};
  await boveda.local.guardar("nube-estado", estado);
  await guardarConfig(llave, repoNuevo, token);
  if (remoto) {  // ya hay datos en la nube: que el usuario elija cuál copia conservar
    await guardarEstado({ problema: "conflicto" });
    avisar();
    return "conflicto";
  }
  await subirAhora();
  return fase === "ok" ? "subido" : fase;
}

export async function desconectar() {
  clearTimeout(temporizador);
  await boveda.local.borrar("nube");
  await boveda.local.borrar("nube-estado");
  cfg = null; estado = {};
  avisar("apagado");
}

/** Dispositivo nuevo: baja la copia de la nube y la descifra con la clave. Devuelve {bytes, llave, sal}. */
export async function recuperar(repoNuevo, token, clave) {
  repoNuevo = repoNuevo.trim(); token = token.trim();
  if (!token) throw new ErrorNube("Escribe el token.", "datos");
  await verificarRepo(token, repoNuevo);
  const remoto = await leerRemoto(token, repoNuevo);
  if (!remoto) throw new ErrorNube("Ese repositorio todavía no tiene una copia de tus datos.", "datos");
  const r = await descifrarRemoto(remoto, clave, null, null);  // lanza ClaveIncorrecta
  estado = {};
  await guardarConfig(r.llave, repoNuevo, token);
  await adoptarRemoto(remoto, r.bytes, r.llave);
  avisar("ok");
  return r;
}

/** Conflicto: quedarse con la copia de la nube (la de este dispositivo se guarda aparte). Luego hay que recargar. */
export async function usarCopiaNube(clave, llave, sal) {
  const remoto = await leerRemoto(cfg.token, cfg.repo);
  if (!remoto) throw new ErrorNube("La nube no tiene copia.", "datos");
  let r;
  try {
    r = await descifrarRemoto(remoto, clave, llave, sal);
  } catch (err) {
    if (err instanceof boveda.ClaveIncorrecta && !clave) {  // la nube usa otra clave: hay que pedirla
      await guardarEstado({ problema: "clave" });
      avisar();
      throw new ErrorNube("La copia de la nube usa otra clave. Escríbela en el recuadro y vuelve a tocar «Usar la de la nube».", "datos");
    }
    throw err;
  }
  await adoptarRemoto(remoto, r.bytes, r.llave);
  return r;
}

/** Conflicto: reemplazar la copia de la nube con la de este dispositivo (la anterior queda en el historial de GitHub). */
export async function usarCopiaLocal() {
  const remoto = await leerRemoto(cfg.token, cfg.repo);
  await guardarEstado({ sha: remoto ? remoto.sha : null, subido: null, problema: null });
  await subirAhora();
  if (fase !== "ok") throw new ErrorNube(detalle || "No se pudo subir.", "red");
}
