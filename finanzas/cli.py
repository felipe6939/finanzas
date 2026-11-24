"""Comandos del sistema: python -m finanzas <comando>.

La Skill de Claude Code usa SOLO estos comandos; nunca escribe en la base directamente.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from datetime import date
from pathlib import Path

from . import config, demo, respaldo
from .core.formato import cop, fecha_corta
from .db.conexion import conectar
from .db.migrar import migrar
from .importers import importacion
from .services import catalogos
from .services.errores import ErrorValidacion

ICONOS = {"lista": "✅", "revisar": "❓", "ya_importada": "↺ ", "ignorada": "·  ", "descartada": "✗ ",
          "importada": "✔ "}


def _con():
    con = conectar()
    migrar(con)
    return con


def _salida(texto: str = "") -> None:
    sys.stdout.write(texto + "\n")


# ------------------------------------------------------------------ servidor

def cmd_iniciar(args) -> None:
    import uvicorn

    from .web.app import crear_app

    import socket

    from . import acceso

    url = f"http://{config.HOST}:{config.PUERTO}"
    app = crear_app()
    direcciones = [config.HOST]
    if acceso.remoto_activo():
        ts = acceso.datos_tailscale()
        if ts["ip"]:
            direcciones.append(ts["ip"])
            app.state.remoto_url = f"http://{ts['nombre'] or ts['ip']}:{config.PUERTO}"
        else:
            _salida("Aviso: el acceso desde el iPhone está activado, pero Tailscale no está conectado en este "
                    "computador. Conéctalo y vuelve a abrir la app.")
    sockets = []
    for host in direcciones:  # solo este equipo y, si aplica, la IP privada de Tailscale; nunca 0.0.0.0
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: que otra copia no comparta el puerto
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((host, config.PUERTO))
        except OSError as e:
            s.close()
            if host == config.HOST:
                raise
            _salida(f"Aviso: no se pudo escuchar en Tailscale ({host}): {e}")
            app.state.remoto_url = None
            continue
        sockets.append(s)
    if not args.sin_navegador:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    _salida(f"Finanzas personales en {url}  (Ctrl+C para detener)")
    if app.state.remoto_url:
        _salida(f"Desde el iPhone (Tailscale): {app.state.remoto_url}")
    servidor = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    servidor.run(sockets=sockets)


# --------------------------------------------------------------- importación

def _resolver_categoria(con, nombre: str, tipo: str) -> int:
    from .core.texto import normalizar

    buscado = normalizar(nombre.replace("›", " ").replace(">", " "))
    candidatas = [c for c in catalogos.listar_categorias(con, tipo, solo_activas=True)
                  if normalizar(c["nombre_completo"].replace("›", " ")) == buscado
                  or normalizar(c["nombre"]) == buscado]
    if len(candidatas) != 1:
        opciones = ", ".join(c["nombre_completo"] for c in candidatas) or "ninguna"
        raise ErrorValidacion(f"Categoría '{nombre}' no encontrada o ambigua (coincidencias: {opciones}).")
    return candidatas[0]["id"]


def _resolver_cuenta(con, nombre: str) -> int:
    from .core.texto import normalizar

    for c in catalogos.listar_cuentas(con, solo_activas=True):
        if normalizar(c["nombre"]) == normalizar(nombre) or normalizar(nombre) in c["alias"].split(","):
            return c["id"]
    raise ErrorValidacion(f"Cuenta '{nombre}' no encontrada.")


def _imprimir_lote(con, lote_id: int, solo_pendientes: bool, como_json: bool) -> None:
    r = importacion.resumen_lote(con, lote_id)
    lineas = r["lineas"]
    if solo_pendientes:
        lineas = [l for l in lineas if l["estado"] == "revisar"]
    if como_json:
        salida = {
            "lote": {k: r["lote"][k] for k in ("id", "estado", "archivo_nombre", "creado_en")},
            "conteo": r["conteo"], "totales": r["totales"], "puede_confirmar": r["puede_confirmar"],
            "lineas": [{k: l[k] for k in ("num_linea", "texto", "estado", "tipo", "fecha", "valor", "descripcion",
                                          "cuenta", "categoria_completa", "meta", "observaciones", "problemas",
                                          "notas", "origen_campos")} for l in lineas],
        }
        _salida(json.dumps(salida, ensure_ascii=False, indent=2, default=str))
        return
    lote = r["lote"]
    _salida(f"Lote #{lote['id']} · {lote['archivo_nombre']} · {lote['estado']}")
    c = r["conteo"]
    _salida(f"  ✅ listas {c['lista']}  ❓ por revisar {c['revisar']}  ↺ ya importadas {c['ya_importada']}  "
            f"· ignoradas {c['ignorada']}  ✗ descartadas {c['descartada']}  ✔ importadas {c['importada']}")
    t = r["totales"]
    _salida(f"  A importar: gastos {cop(t['gasto'])} · ingresos {cop(t['ingreso'])} · ahorro {cop(t['ahorro'])}")
    _salida("")
    for l in lineas:
        icono = ICONOS.get(l["estado"], "?")
        if l["estado"] in ("ignorada", "ya_importada") and not solo_pendientes:
            _salida(f"{icono} L{l['num_linea']:<3} {l['texto']}")
            continue
        detalle = (f"{l['tipo'] or '?'} · {fecha_corta(l['fecha'])} · {cop(l['valor'])} · "
                   f"{l['descripcion'] or '?'} · {l['cuenta'] or '?'} · "
                   f"{l['categoria_completa'] or l['meta'] or ('—' if l['tipo'] == 'ahorro' else '?')}")
        _salida(f"{icono} L{l['num_linea']:<3} «{l['texto']}»")
        _salida(f"        → {detalle}")
        for p in l["problemas"]:
            _salida(f"        {'ERROR' if p['nivel'] == 'error' else 'AVISO'}: {p['texto']}")
        for n in l["notas"]:
            _salida(f"        nota: {n}")
    if r["puede_confirmar"]:
        _salida(f"\nListo para confirmar: python -m finanzas importar confirmar {lote_id}")


def cmd_importar(args) -> None:
    con = _con()
    accion = args.accion
    if accion == "preparar":
        if args.texto:
            lote_id = importacion.preparar_texto(con, Path(args.archivo).read_text(encoding="utf-8")
                                                 if args.archivo != "-" else sys.stdin.read())
        else:
            lote_id = importacion.preparar_archivo(con, Path(args.archivo))
        _imprimir_lote(con, lote_id, solo_pendientes=False, como_json=args.json)
    elif accion == "ver":
        _imprimir_lote(con, args.lote, solo_pendientes=args.pendientes, como_json=args.json)
    elif accion == "corregir":
        lineas = {l["num_linea"]: l for l in importacion.lineas_del_lote(con, args.lote)}
        if args.linea not in lineas:
            raise ErrorValidacion(f"El lote #{args.lote} no tiene la línea {args.linea}.")
        actual = lineas[args.linea]
        cambios = {}
        if args.tipo:
            cambios["tipo"] = args.tipo
        tipo = args.tipo or actual["tipo"]
        if args.fecha:
            cambios["fecha"] = date.fromisoformat(args.fecha).isoformat()
        if args.valor is not None:
            cambios["valor"] = args.valor
        if args.descripcion:
            cambios["descripcion"] = args.descripcion
        if args.cuenta:
            cambios["cuenta_id"] = _resolver_cuenta(con, args.cuenta)
        if args.categoria:
            cambios["categoria_id"] = _resolver_categoria(con, args.categoria, tipo)
        if args.observaciones is not None:
            cambios["observaciones"] = args.observaciones
        importacion.corregir(
            con, args.lote, args.linea, cambios,
            aceptar_avisos=True if args.aceptar else None,
            descartar=True if args.descartar else (False if args.restaurar else None),
            recordar_patron=args.recordar,
        )
        _imprimir_lote(con, args.lote, solo_pendientes=False, como_json=False)
    elif accion == "aceptar-avisos":
        n = importacion.aceptar_todos_los_avisos(con, args.lote)
        _salida(f"Avisos aceptados en {n} líneas.")
    elif accion == "confirmar":
        r = importacion.confirmar(con, args.lote)
        _salida(f"Importados {r['importados']} movimientos ({cop(r['total'])}). Respaldo previo: {r['respaldo'].name}")
    elif accion == "descartar":
        importacion.descartar_lote(con, args.lote)
        _salida(f"Lote #{args.lote} descartado. No se registró nada.")
    elif accion == "revertir":
        n = importacion.revertir(con, args.lote)
        _salida(f"Lote #{args.lote} revertido: {n} movimientos eliminados (borrado lógico, auditado).")
    elif accion == "lotes":
        for l in importacion.listar_lotes(con):
            _salida(f"#{l['id']:<4} {l['creado_en']}  {l['estado']:<11} {l['movimientos']:>4} mov.  "
                    f"{l['por_revisar']:>3} por revisar  {l['archivo_nombre']}")


# ---------------------------------------------------------------- catálogos

def cmd_categorias(args) -> None:
    con = _con()
    for tipo in ("gasto", "ingreso"):
        _salida(f"== {tipo.upper()} ==")
        for c in catalogos.arbol_categorias(con, tipo):
            if not c["activa"]:
                continue
            _salida(f"{c['nombre']}")
            for h in c["hijas"]:
                if h["activa"]:
                    _salida(f"   {c['nombre']} › {h['nombre']}  ({h['naturaleza_efectiva']}"
                            f"{', ' + h['necesidad_efectiva'] if h['necesidad_efectiva'] else ''})")


def cmd_cuentas(args) -> None:
    con = _con()
    for c in catalogos.listar_cuentas(con):
        marca = " (por defecto)" if c["por_defecto"] else ""
        _salida(f"{c['nombre']}{marca}: saldo inicial {cop(c['saldo_inicial'])} · alias: {c['alias']}"
                f"{'' if c['activa'] else ' · INACTIVA'}")
    _salida(f"Fecha de inicio: {fecha_corta(catalogos.fecha_inicio(con))} · "
            f"Ahorro inicial: {cop(catalogos.ahorro_inicial(con))}")


def cmd_punto_partida(args) -> None:
    from .services import punto_partida
    con = _con()
    for h in punto_partida.registrar_como_ingresos(con):
        _salida(f"Ingreso «Punto de partida» en {h['cuenta']}: {cop(h['valor'])}")
    _salida("Saldos iniciales en 0. El disponible no cambia.")


# ------------------------------------------------------------ mantenimiento

def cmd_respaldo(args) -> None:
    if args.accion == "crear":
        con = _con()
        _salida(f"Respaldo creado: {respaldo.crear_respaldo(con, motivo='manual')}")
    elif args.accion == "listar":
        for r in respaldo.listar_respaldos():
            _salida(f"{r.name}  ({r.stat().st_size // 1024} KB)")
    elif args.accion == "restaurar":
        ruta = Path(args.archivo)
        if not ruta.is_absolute():
            ruta = config.dir_respaldos() / ruta
        seguridad = respaldo.restaurar(ruta)
        _salida(f"Base restaurada desde {ruta.name}. La base anterior quedó en {seguridad.name}.")


def cmd_exportar(args) -> None:
    con = _con()
    _salida(f"Exportado: {respaldo.exportar(con, args.formato)}")


def cmd_demo(args) -> None:
    con = _con()
    if args.accion == "cargar":
        _salida(f"Cargados {demo.cargar(con)} movimientos de ejemplo (FICTICIOS).")
    else:
        _salida(f"Eliminados {demo.eliminar(con)} movimientos de ejemplo. Configuración previa restaurada.")


def cmd_reiniciar(args) -> None:
    if not args.confirmar:
        raise ErrorValidacion("Esto borra TODOS los movimientos e importaciones. Repite con --confirmar.")
    con = _con()
    r = demo.reiniciar(con, args.fecha_inicio)
    _salida(f"Sistema reiniciado: {r['movimientos_borrados']} movimientos borrados. Respaldo: {r['respaldo'].name}")


def cmd_migrar(args) -> None:
    con = conectar()
    aplicadas = migrar(con)
    _salida(f"Migraciones aplicadas: {aplicadas}" if aplicadas else "El esquema ya está al día.")


# --------------------------------------------------------------------- main

def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finanzas", description="Sistema de finanzas personales")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("iniciar", help="Abre la aplicación web local")
    s.add_argument("--sin-navegador", action="store_true")
    s.set_defaults(fn=cmd_iniciar)

    s = sub.add_parser("importar", help="Importación por lotes")
    acc = s.add_subparsers(dest="accion", required=True)
    a = acc.add_parser("preparar", help="Crea un lote en borrador desde un archivo (.docx/.txt)")
    a.add_argument("archivo", help="Ruta del archivo, o '-' con --texto para leer de la entrada estándar")
    a.add_argument("--texto", action="store_true", help="Interpretar el archivo/entrada como texto pegado")
    a.add_argument("--json", action="store_true")
    a = acc.add_parser("ver")
    a.add_argument("lote", type=int)
    a.add_argument("--pendientes", action="store_true", help="Solo las líneas por revisar")
    a.add_argument("--json", action="store_true")
    a = acc.add_parser("corregir")
    a.add_argument("lote", type=int)
    a.add_argument("linea", type=int)
    a.add_argument("--tipo", choices=["gasto", "ingreso", "ahorro"])
    a.add_argument("--fecha", help="AAAA-MM-DD")
    a.add_argument("--valor", type=int)
    a.add_argument("--descripcion")
    a.add_argument("--cuenta")
    a.add_argument("--categoria", help="'Transporte › Gasolina' o 'Gasolina'")
    a.add_argument("--observaciones")
    a.add_argument("--aceptar", action="store_true", help="Aceptar los avisos (posible duplicado, etc.)")
    a.add_argument("--descartar", action="store_true", help="No importar esta línea")
    a.add_argument("--restaurar", action="store_true", help="Volver a incluir una línea descartada")
    a.add_argument("--recordar", help="Palabra clave a recordar para esta categoría")
    for nombre in ("aceptar-avisos", "confirmar", "descartar", "revertir"):
        a = acc.add_parser(nombre)
        a.add_argument("lote", type=int)
    acc.add_parser("lotes")
    s.set_defaults(fn=cmd_importar)

    sub.add_parser("categorias").set_defaults(fn=cmd_categorias)
    sub.add_parser("cuentas").set_defaults(fn=cmd_cuentas)
    sub.add_parser("punto-partida-a-ingresos",
                   help="Registra los saldos iniciales como ingresos del primer mes (una sola vez)"
                   ).set_defaults(fn=cmd_punto_partida)

    s = sub.add_parser("respaldo")
    acc = s.add_subparsers(dest="accion", required=True)
    acc.add_parser("crear")
    acc.add_parser("listar")
    a = acc.add_parser("restaurar", help="Detén la aplicación web antes de restaurar")
    a.add_argument("archivo")
    s.set_defaults(fn=cmd_respaldo)

    s = sub.add_parser("exportar")
    s.add_argument("--formato", choices=["xlsx", "csv"], default="xlsx")
    s.set_defaults(fn=cmd_exportar)

    s = sub.add_parser("demo", help="Datos de ejemplo ficticios")
    s.add_argument("accion", choices=["cargar", "eliminar"])
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("reiniciar", help="Borra todos los movimientos para empezar con datos reales")
    s.add_argument("--confirmar", action="store_true")
    s.add_argument("--fecha-inicio", help="AAAA-MM-DD")
    s.set_defaults(fn=cmd_reiniciar)

    sub.add_parser("migrar").set_defaults(fn=cmd_migrar)
    return p


def main(argv: list[str] | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    args = construir_parser().parse_args(argv)
    try:
        args.fn(args)
    except ErrorValidacion as e:
        for err in e.errores:
            sys.stderr.write(f"ERROR: {err}\n")
        return 2
    return 0
