// Bóveda: cifra la base SQLite con la clave del usuario y la guarda en el dispositivo (IndexedDB).
//
// Formato del archivo cifrado («sobre»), el mismo que se sube a GitHub y que lee la app del PC:
//   FINANZAS-CIFRADO-1\n
//   {"v":1,"kdf":"PBKDF2-SHA256","iteraciones":600000,"sal":"<b64>","iv":"<b64>","compresion":"gzip",...}\n
//   <bytes cifrados con AES-256-GCM>
// Contenido = gzip(base SQLite). La cabecera (las dos primeras líneas) va como «datos asociados»
// de AES-GCM: si alguien la altera, el descifrado falla. La clave nunca se guarda.

const MARCA = "FINANZAS-CIFRADO-1";
export const ITERACIONES = 600000;
const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64(bytes) {  // por bloques: String.fromCharCode(...x) falla con arreglos grandes
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
export const deB64 = (texto) => Uint8Array.from(atob(texto.replace(/\s/g, "")), (c) => c.charCodeAt(0));

export class ClaveIncorrecta extends Error {}

export async function derivarLlave(clave, sal, iteraciones = ITERACIONES) {
  const base = await crypto.subtle.importKey("raw", enc.encode(clave.normalize("NFC")), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt: sal, iterations: iteraciones },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

export function salNueva() { return crypto.getRandomValues(new Uint8Array(16)); }

async function transformar(bytes, stream) {
  return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(stream)).arrayBuffer());
}

/** Cifra `datos` (la base SQLite) y devuelve el sobre completo. */
export async function cifrar(llave, sal, datos, extra = {}) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cab = { v: 1, kdf: "PBKDF2-SHA256", iteraciones: ITERACIONES, sal: b64(sal), iv: b64(iv),
                compresion: "gzip", creado: new Date().toISOString(), ...extra };
  const cabecera = enc.encode(MARCA + "\n" + JSON.stringify(cab) + "\n");
  const comprimido = await transformar(datos, new CompressionStream("gzip"));
  const cifrado = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: cabecera }, llave, comprimido));
  const sobre = new Uint8Array(cabecera.length + cifrado.length);
  sobre.set(cabecera, 0);
  sobre.set(cifrado, cabecera.length);
  return sobre;
}

/** Lee la cabecera del sobre sin descifrar. */
export function leerCabecera(sobre) {
  const n1 = sobre.indexOf(10);
  const n2 = sobre.indexOf(10, n1 + 1);
  if (n1 < 0 || n2 < 0 || dec.decode(sobre.subarray(0, n1)) !== MARCA) throw new Error("El archivo no es una copia cifrada de Finanzas.");
  const cab = JSON.parse(dec.decode(sobre.subarray(n1 + 1, n2)));
  return { cab, cabecera: sobre.subarray(0, n2 + 1), cifrado: sobre.subarray(n2 + 1), sal: deB64(cab.sal), iv: deB64(cab.iv) };
}

/** Descifra el sobre. Lanza ClaveIncorrecta si la llave no corresponde. */
export async function descifrar(llave, sobre) {
  const { cabecera, cifrado, iv } = leerCabecera(sobre);
  let comprimido;
  try {
    comprimido = await crypto.subtle.decrypt({ name: "AES-GCM", iv, additionalData: cabecera }, llave, cifrado);
  } catch {
    throw new ClaveIncorrecta("Clave incorrecta.");
  }
  return transformar(new Uint8Array(comprimido), new DecompressionStream("gzip"));
}

/** Cifra un texto corto (el token de GitHub) con la llave de la sesión. */
export async function cifrarTexto(llave, texto) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const datos = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, llave, enc.encode(texto)));
  return { iv, datos };
}

export async function descifrarTexto(llave, { iv, datos }) {
  try {
    return dec.decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, llave, datos));
  } catch {
    throw new ClaveIncorrecta("Clave incorrecta.");
  }
}

export async function huella(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
}

// ------------------------------------------------------------------ IndexedDB

const NOMBRE_BD = "finanzas";
const ALMACEN = "datos";

function abrirBD() {
  return new Promise((ok, mal) => {
    const req = indexedDB.open(NOMBRE_BD, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(ALMACEN);
    req.onsuccess = () => ok(req.result);
    req.onerror = () => mal(req.error);
  });
}

async function operar(modo, fn) {
  const bd = await abrirBD();
  try {
    return await new Promise((ok, mal) => {
      const tx = bd.transaction(ALMACEN, modo);
      const req = fn(tx.objectStore(ALMACEN));
      tx.oncomplete = () => ok(req && req.result);
      tx.onerror = tx.onabort = () => mal(tx.error);
    });
  } finally {
    bd.close();
  }
}

export const local = {
  leer: (clave) => operar("readonly", (s) => s.get(clave)),
  guardar: (clave, valor) => operar("readwrite", (s) => s.put(valor, clave)),
  borrar: (clave) => operar("readwrite", (s) => s.delete(clave)),
};

/** Pide al navegador no borrar estos datos por falta de espacio (Safari/Chrome). */
export async function pedirPersistencia() {
  try { return navigator.storage && navigator.storage.persist ? await navigator.storage.persist() : false; }
  catch { return false; }
}
