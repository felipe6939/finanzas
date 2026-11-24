"""Puente entre el navegador y la app FastAPI, sin servidor (versión GitHub Pages).

En el navegador (Pyodide) no hay red ni uvicorn: `motor.js` intercepta cada clic y cada
formulario y llama a `peticion()`, que entrega la petición directamente a la app ASGI
—la misma `crear_app()` del PC— y devuelve la respuesta. Los redireccionamientos se siguen
aquí, y las rutas `/static/...` del HTML se reescriben hacia los archivos publicados.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urljoin, urlsplit

_app = None
_prefijo_static = "/static/"

MAX_REDIRECCIONES = 10
_RE_STATIC = re.compile(r'(?<=["\'(])/static/')


def sin_hilos() -> None:
    """El navegador no permite hilos: las rutas `def` de FastAPI (que Starlette corre en un
    hilo aparte) se ejecutan directamente. Un solo usuario y una petición a la vez: es seguro."""
    import anyio.to_thread

    async def directo(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
        return func(*args)

    anyio.to_thread.run_sync = directo


def iniciar(prefijo_static: str = "/static/"):
    """Crea la app (migra la base si hace falta). `prefijo_static`: URL pública de static/."""
    global _app, _prefijo_static
    import sys

    from .app import crear_app

    if sys.platform == "emscripten":
        sin_hilos()
    _prefijo_static = prefijo_static
    _app = crear_app()
    return _app


async def _llamar(metodo: str, url: str, cabeceras: list[tuple[str, str]], cuerpo: bytes) -> dict:
    partes = urlsplit(url)
    ruta = partes.path or "/"
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": metodo.upper(), "scheme": "https", "path": unquote(ruta),
        "raw_path": quote(unquote(ruta)).encode(), "query_string": partes.query.encode(), "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in cabeceras],
        "client": ("127.0.0.1", 0), "server": ("finanzas", 443),
    }
    enviado = False

    async def receive():
        nonlocal enviado
        if not enviado:
            enviado = True
            return {"type": "http.request", "body": cuerpo, "more_body": False}
        return {"type": "http.disconnect"}

    respuesta = {"estado": 500, "cabeceras": {}, "cuerpo": b""}
    trozos: list[bytes] = []

    async def send(mensaje):
        if mensaje["type"] == "http.response.start":
            respuesta["estado"] = mensaje["status"]
            respuesta["cabeceras"] = {k.decode("latin-1").lower(): v.decode("latin-1")
                                      for k, v in mensaje.get("headers", [])}
        elif mensaje["type"] == "http.response.body":
            trozos.append(mensaje.get("body", b""))

    await _app(scope, receive, send)
    respuesta["cuerpo"] = b"".join(trozos)
    return respuesta


async def peticion(metodo: str, url: str, cabeceras: list | None = None, cuerpo: bytes | None = None) -> dict:
    """Ejecuta la petición siguiendo redirecciones.

    Devuelve: estado, url (la final, tras redirecciones), tipo (content-type), cabeceras,
    cuerpo (bytes; el HTML ya reescrito) y `modifico` (True si fue un POST que respondió bien:
    la base pudo cambiar y hay que guardarla).
    """
    if _app is None:
        raise RuntimeError("Llama primero a puente.iniciar().")
    if hasattr(cabeceras, "to_py"):  # desde JavaScript (Pyodide)
        cabeceras = cabeceras.to_py()
    if hasattr(cuerpo, "to_bytes"):
        cuerpo = cuerpo.to_bytes()
    cabeceras = [(str(k), str(v)) for k, v in (cabeceras or [])]
    cuerpo = bytes(cuerpo or b"")
    modifico = metodo.upper() not in ("GET", "HEAD")
    r = await _llamar(metodo, url, cabeceras, cuerpo)
    for _ in range(MAX_REDIRECCIONES):
        if r["estado"] not in (301, 302, 303, 307, 308) or "location" not in r["cabeceras"]:
            break
        destino = urljoin(url, r["cabeceras"]["location"])
        partes = urlsplit(destino)
        url = partes.path + (f"?{partes.query}" if partes.query else "") + (f"#{partes.fragment}" if partes.fragment else "")
        r = await _llamar("GET", url, [], b"")
    tipo = r["cabeceras"].get("content-type", "")
    cuerpo_resp, texto = r["cuerpo"], None
    if tipo.startswith("text/html"):
        texto = _RE_STATIC.sub(_prefijo_static, cuerpo_resp.decode("utf-8"))
        cuerpo_resp = texto.encode("utf-8")
    return {"estado": r["estado"], "url": url, "tipo": tipo, "cabeceras": r["cabeceras"],
            "cuerpo": cuerpo_resp, "texto": texto, "modifico": modifico and r["estado"] < 400}


_TABLAS_REGISTROS = ("movimientos", "transferencias", "prestamos", "abonos_prestamo")


def revisar_base(ruta: str) -> dict:
    """Antes de guardar o subir: integridad de la base y cuántos registros tiene.

    `registros` cuenta también los eliminados (se marcan con `eliminado_en`, no se borran):
    el número solo baja si se borra en bloque (datos de ejemplo, reinicio) o si la copia es
    vieja/dañada. Con eso la nube frena una subida que perdería datos.
    """
    import sqlite3

    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        existentes = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        registros = sum(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        for t in _TABLAS_REGISTROS if t in existentes)
    except sqlite3.DatabaseError as e:
        return {"integridad": f"base dañada: {e}", "registros": 0}
    finally:
        con.close()
    return {"integridad": integridad, "registros": registros}
