// Prueba de la sincronización (nube.js) con un GitHub simulado en memoria. Datos ficticios.
// Uso: node navegador/pruebas/prueba_nube.mjs   (la ejecuta tests/test_nube_js.py)

import assert from "node:assert/strict";

globalThis.window = new EventTarget();
const boveda = await import("../boveda.js");
const nube = await import("../nube.js");

// ---------------------------------------------------------------- almacenamiento local simulado
const almacen = new Map();
boveda.local.leer = async (k) => almacen.get(k);
boveda.local.guardar = async (k, v) => { almacen.set(k, structuredClone(v)); };
boveda.local.borrar = async (k) => { almacen.delete(k); };

// ---------------------------------------------------------------- GitHub simulado
const gh = { privado: true, archivo: null, sinRed: false, commits: 0, tokenValido: "tok-prueba" };
let contador = 0;
const responder = (estado, cuerpo) => new Response(cuerpo == null ? null : JSON.stringify(cuerpo), { status: estado });

globalThis.fetch = async (url, op = {}) => {
  if (gh.sinRed) throw new TypeError("Failed to fetch");
  const u = new URL(url);
  assert.equal(u.origin, "https://api.github.com");
  if (op.headers.Authorization !== `Bearer ${gh.tokenValido}`) return responder(401, { message: "Bad credentials" });
  const m = u.pathname.match(/^\/repos\/u\/datos(\/contents\/(.+))?$/);
  if (!m) return responder(404, {});
  if (!m[1]) return responder(200, { private: gh.privado });
  const metodo = op.method || "GET";
  if (metodo === "GET") {
    if (!gh.archivo) return responder(404, {});
    const b = boveda.b64(gh.archivo.sobre);
    return responder(200, { sha: gh.archivo.sha, encoding: "base64", content: b.replace(/(.{60})/g, "$1\n") });
  }
  if (metodo === "PUT") {
    const cuerpo = JSON.parse(op.body);
    if (gh.archivo && !cuerpo.sha) return responder(422, { message: "sha wasn't supplied" });
    if (gh.archivo && cuerpo.sha !== gh.archivo.sha) return responder(409, { message: "does not match" });
    gh.archivo = { sha: "sha" + ++contador, sobre: boveda.deB64(cuerpo.content) };
    gh.commits++;
    return responder(gh.archivo.sha === "sha1" ? 201 : 200, { content: { sha: gh.archivo.sha } });
  }
  return responder(405, {});
};

const esperar = (ms) => new Promise((ok) => setTimeout(ok, ms));
const texto = (bytes) => new TextDecoder().decode(bytes);
const CLAVE = "clave-de-prueba-1";

async function guardarLocal(llave, sal, contenido) {
  const sobre = await boveda.cifrar(llave, sal, new TextEncoder().encode(contenido));
  await esperar(2);  // marcas de tiempo distintas
  await boveda.local.guardar("base", { sobre, guardado: new Date().toISOString(), bytes: contenido.length });
}

/** Otro dispositivo sube una versión nueva (con la clave dada). */
async function subidaAjena(contenido, clave = CLAVE) {
  const sal = boveda.salNueva();
  const llave = await boveda.derivarLlave(clave, sal);
  const sobre = await boveda.cifrar(llave, sal, new TextEncoder().encode(contenido));
  gh.archivo = { sha: "ajeno" + ++contador, sobre };
}

async function abrir(clave) {  // igual que motor.js al abrir
  const base = await boveda.local.leer("base");
  const { sal } = boveda.leerCabecera(base.sobre);
  const llave = await boveda.derivarLlave(clave, sal);
  let bytes = await boveda.descifrar(llave, base.sobre);
  let ses = { llave, sal };
  if (await nube.cargar(llave)) {
    const r = await nube.alAbrir(clave, llave, sal, base);
    if (r) { bytes = r.bytes; ses = { llave: r.llave, sal: r.sal }; }
  }
  return { ...ses, contenido: texto(bytes) };
}

let paso = "";
const prueba = async (nombre, fn) => { paso = nombre; await fn(); console.log("ok -", nombre); };

try {
  // ---------------------------------------------------------------- dispositivo A
  const salA = boveda.salNueva();
  const llaveA = await boveda.derivarLlave(CLAVE, salA);
  await guardarLocal(llaveA, salA, "v1");
  await nube.cargar(llaveA);

  await prueba("rechaza repositorio público", async () => {
    gh.privado = false;
    await assert.rejects(nube.conectar(llaveA, "u/datos", "tok-prueba"), (e) => e.tipo === "publico");
    assert.equal(nube.configurada(), false);
    gh.privado = true;
  });

  await prueba("rechaza token inválido", async () => {
    await assert.rejects(nube.conectar(llaveA, "u/datos", "otro"), (e) => e.tipo === "token");
  });

  await prueba("conectar sube la copia cifrada", async () => {
    assert.equal(await nube.conectar(llaveA, "u/datos", " tok-prueba "), "subido");
    assert.equal(gh.commits, 1);
    assert.equal(texto(await boveda.descifrar(llaveA, gh.archivo.sobre)), "v1");
    const cfg = almacen.get("nube");
    assert.ok(!JSON.stringify([...cfg.token.datos]).includes("tok"), "el token no se guarda en claro");
    assert.notEqual(texto(cfg.token.datos), "tok-prueba");
    assert.equal(nube.estadoActual().fase, "ok");
  });

  await prueba("los cambios se suben agrupados", async () => {
    await guardarLocal(llaveA, salA, "v2"); nube.programarSubida();
    await guardarLocal(llaveA, salA, "v3"); nube.programarSubida();
    await esperar(2600);
    assert.equal(gh.commits, 2);
    assert.equal(texto(await boveda.descifrar(llaveA, gh.archivo.sobre)), "v3");
  });

  await prueba("sin internet queda pendiente y sube al volver", async () => {
    gh.sinRed = true;
    await guardarLocal(llaveA, salA, "v4");
    await nube.subirAhora();
    assert.equal(nube.estadoActual().fase, "pendiente");
    assert.equal(await nube.hayPendiente(), true);
    gh.sinRed = false;
    await nube.subirAhora();
    assert.equal(nube.estadoActual().fase, "ok");
    assert.equal(texto(await boveda.descifrar(llaveA, gh.archivo.sobre)), "v4");
  });

  await prueba("al abrir baja lo nuevo de otro dispositivo", async () => {
    await subidaAjena("v5-iphone");
    const r = await abrir(CLAVE);
    assert.equal(r.contenido, "v5-iphone");
    assert.equal(texto(await boveda.descifrar(r.llave, almacen.get("base").sobre)), "v5-iphone");
    assert.equal(texto(await boveda.descifrar(llaveA, almacen.get("base-anterior").sobre)), "v4");
    assert.equal(await nube.hayPendiente(), false);
  });

  await prueba("abrir sin novedades no baja ni sube nada", async () => {
    const commits = gh.commits;
    const r = await abrir(CLAVE);
    assert.equal(r.contenido, "v5-iphone");
    await nube.subirAhora();
    assert.equal(gh.commits, commits);
  });

  await prueba("conflicto: avisa, no mezcla y no sube", async () => {
    const ses = await abrir(CLAVE);
    await guardarLocal(ses.llave, ses.sal, "v6-pc");   // cambio aquí...
    gh.sinRed = true; await nube.subirAhora(); gh.sinRed = false;
    await subidaAjena("v6-iphone");                     // ...y allá
    const r = await abrir(CLAVE);
    assert.equal(r.contenido, "v6-pc", "se sigue viendo la copia local");
    assert.equal(nube.estadoActual().problema, "conflicto");
    const sha = gh.archivo.sha;
    await nube.subirAhora();
    assert.equal(gh.archivo.sha, sha, "no se sube nada en conflicto");
    assert.equal(texto(await boveda.descifrar(ses.llave, gh.archivo.sobre).catch(() => new TextEncoder().encode("?"))), "?");  // otra sal
  });

  await prueba("conflicto resuelto: usar la de este dispositivo", async () => {
    await nube.usarCopiaLocal();
    assert.equal(nube.estadoActual().problema, null);
    const ses = await abrir(CLAVE);
    assert.equal(ses.contenido, "v6-pc");
    assert.equal(texto(await boveda.descifrar(ses.llave, gh.archivo.sobre)), "v6-pc");
  });

  await prueba("conflicto resuelto: usar la de la nube", async () => {
    const ses = await abrir(CLAVE);
    await guardarLocal(ses.llave, ses.sal, "v7-pc");
    gh.sinRed = true; await nube.subirAhora(); gh.sinRed = false;
    await subidaAjena("v7-iphone");
    await abrir(CLAVE);
    assert.equal(nube.estadoActual().problema, "conflicto");
    await nube.usarCopiaNube(CLAVE, null, null);
    const r = await abrir(CLAVE);
    assert.equal(r.contenido, "v7-iphone");
    assert.equal(texto(await boveda.descifrar(ses.llave, almacen.get("base-anterior").sobre)), "v7-pc");
  });

  await prueba("la clave cambió en otro dispositivo", async () => {
    await subidaAjena("v8-clave-nueva", "otra-clave-9");
    const r = await abrir(CLAVE);
    assert.equal(r.contenido, "v7-iphone");
    assert.equal(nube.estadoActual().problema, "clave");
    await assert.rejects(nube.usarCopiaNube("equivocada", null, null), boveda.ClaveIncorrecta);
    await nube.usarCopiaNube("otra-clave-9", null, null);
    await assert.rejects(abrir(CLAVE), boveda.ClaveIncorrecta);
    const r2 = await abrir("otra-clave-9");
    assert.equal(r2.contenido, "v8-clave-nueva");
    assert.equal(nube.configurada(), true, "el token sigue sirviendo con la clave nueva");
  });

  await prueba("cambio de clave aquí: el token se vuelve a cifrar", async () => {
    const sal = boveda.salNueva();
    const llave = await boveda.derivarLlave("clave-nueva-10", sal);
    await nube.alCambiarLlave(llave);
    await guardarLocal(llave, sal, "v9");
    await nube.subirAhora();
    const r = await abrir("clave-nueva-10");
    assert.equal(r.contenido, "v9");
    assert.equal(nube.configurada(), true);
  });

  // ---------------------------------------------------------------- dispositivo nuevo
  await prueba("recuperar en un dispositivo nuevo", async () => {
    almacen.clear();
    await nube.desconectar();
    await assert.rejects(nube.recuperar("u/datos", "tok-prueba", "mala-clave"), boveda.ClaveIncorrecta);
    assert.equal(almacen.has("base"), false);
    const r = await nube.recuperar("u/datos", "tok-prueba", "clave-nueva-10");
    assert.equal(texto(r.bytes), "v9");
    const ses = await abrir("clave-nueva-10");
    assert.equal(ses.contenido, "v9");
    assert.equal(await nube.hayPendiente(), false);
  });

  await prueba("recuperar sin copia en la nube", async () => {
    gh.archivo = null;
    await assert.rejects(nube.recuperar("u/datos", "tok-prueba", "x"), (e) => e.tipo === "datos");
  });

  console.log("TODO BIEN");
  process.exit(0);
} catch (err) {
  console.error(`FALLÓ en «${paso}»:`, err);
  process.exit(1);
}
