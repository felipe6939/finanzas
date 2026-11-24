// Desbloqueo con Face ID / Touch ID / Windows Hello (WebAuthn con la extensión PRF).
//
// Al activarlo se crea una «llave de acceso» (passkey) de este sitio. Con Face ID, el dispositivo
// entrega un secreto (PRF) que solo existe dentro de esa llave; con él se cifra tu clave y se guarda
// en este dispositivo. Al abrir: Face ID → secreto → se descifra la clave → se abre como siempre.
// Sin tu cara/huella el secreto no sale del dispositivo, y sin el secreto la clave guardada no sirve.
//
// IndexedDB "biometria" → {credId, salPrf, iv, datos, creado}. Si cambias la clave, se desactiva.

import * as boveda from "./boveda.js";

const enc = new TextEncoder();

export class SinSoporte extends Error {}
export class Cancelado extends Error {}

export const nombre = () => (/iPhone|iPad/.test(navigator.userAgent) ? "Face ID"
  : /Macintosh/.test(navigator.userAgent) ? "Touch ID" : "Windows Hello");

export async function disponible() {
  try {
    return !!window.PublicKeyCredential &&
      await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch { return false; }
}

export async function activa() { return !!(await boveda.local.leer("biometria")); }

async function llaveDesdePrf(secreto) {
  const base = await crypto.subtle.importKey("raw", secreto, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: enc.encode("finanzas-biometria-v1") },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

function traducirError(err) {
  if (err && (err.name === "NotAllowedError" || err.name === "AbortError")) return new Cancelado("Se canceló o no se reconoció.");
  return err;
}

async function pedirSecreto(credId, salPrf) {
  let cred;
  try {
    cred = await navigator.credentials.get({ publicKey: {
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      allowCredentials: [{ type: "public-key", id: credId }],
      userVerification: "required",
      timeout: 60000,
      extensions: { prf: { eval: { first: salPrf } } },
    } });
  } catch (err) { throw traducirError(err); }
  const prf = cred.getClientExtensionResults().prf;
  if (!prf || !prf.results || !prf.results.first) throw new SinSoporte("Este dispositivo no entrega el secreto necesario (PRF).");
  return new Uint8Array(prf.results.first);
}

/** Activa el desbloqueo: crea la llave de acceso y guarda la clave cifrada con su secreto. */
export async function activar(clave) {
  if (!(await disponible())) throw new SinSoporte(`Este dispositivo no tiene ${nombre()} disponible para sitios web.`);
  const salPrf = crypto.getRandomValues(new Uint8Array(32));
  let cred;
  try {
    cred = await navigator.credentials.create({ publicKey: {
      rp: { name: "Finanzas personales" },
      user: { id: crypto.getRandomValues(new Uint8Array(16)), name: "Finanzas personales", displayName: "Finanzas personales" },
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { authenticatorAttachment: "platform", residentKey: "required", userVerification: "required" },
      timeout: 60000,
      extensions: { prf: { eval: { first: salPrf } } },
    } });
  } catch (err) { throw traducirError(err); }
  const credId = new Uint8Array(cred.rawId);
  const prf = cred.getClientExtensionResults().prf;
  if (prf && prf.enabled === false) throw new SinSoporte(`${nombre()} de este dispositivo no permite guardar la clave de forma segura (sin PRF).`);
  // Algunos dispositivos entregan el secreto al crear; otros solo al usar la llave (segunda confirmación).
  const secreto = prf && prf.results && prf.results.first ? new Uint8Array(prf.results.first) : await pedirSecreto(credId, salPrf);
  const { iv, datos } = await boveda.cifrarTexto(await llaveDesdePrf(secreto), clave);
  await boveda.local.guardar("biometria", { credId, salPrf, iv, datos, creado: new Date().toISOString() });
}

/** Face ID → devuelve la clave guardada. */
export async function desbloquear() {
  const g = await boveda.local.leer("biometria");
  if (!g) throw new SinSoporte("El desbloqueo no está activado en este dispositivo.");
  const secreto = await pedirSecreto(g.credId, g.salPrf);
  try {
    return await boveda.descifrarTexto(await llaveDesdePrf(secreto), { iv: g.iv, datos: g.datos });
  } catch {
    throw new SinSoporte("No se pudo leer la clave guardada. Desactiva y vuelve a activar el desbloqueo.");
  }
}

export async function desactivar() { await boveda.local.borrar("biometria"); }
