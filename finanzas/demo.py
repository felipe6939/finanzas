"""Datos de ejemplo (FICTICIOS) para probar el sistema, y reinicio para empezar con datos reales.

Los movimientos de ejemplo llevan origen = 'demo' y pueden borrarse por completo.
Al cargarlos se guarda la configuración previa (fecha de inicio y saldos) para
restaurarla al eliminarlos.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .core.fechas import hoy
from .db.conexion import transaccion
from .respaldo import crear_respaldo
from .services import catalogos, movimientos
from .services.errores import ErrorValidacion

INICIO_DEMO = date(2026, 9, 1)
SALDOS_DEMO = {"Nequi": 350_000, "Bancolombia": 2_100_000, "BBVA": 800_000, "Efectivo": 120_000}


def demo_activo(con: sqlite3.Connection) -> bool:
    return catalogos.obtener_config(con, "demo_estado_previo") is not None


def _cat(con: sqlite3.Connection, tipo: str, nombre: str) -> int:
    fila = con.execute(
        "SELECT id FROM categorias WHERE tipo = ? AND nombre = ? AND padre_id IS NOT NULL", (tipo, nombre)
    ).fetchone()
    if not fila:
        raise RuntimeError(f"Falta la categoría base '{nombre}'.")
    return fila["id"]


def _cuentas(con: sqlite3.Connection) -> dict[str, int]:
    return {c["nombre"]: c["id"] for c in catalogos.listar_cuentas(con)}


def cargar(con: sqlite3.Connection) -> int:
    if demo_activo(con):
        raise ErrorValidacion("Los datos de ejemplo ya están cargados.")
    if con.execute("SELECT 1 FROM movimientos WHERE origen <> 'demo' AND eliminado_en IS NULL LIMIT 1").fetchone():
        raise ErrorValidacion("Ya hay movimientos reales: no se cargan datos de ejemplo encima de ellos.")
    crear_respaldo(con, motivo="antes-de-cargar-demo")
    cu = _cuentas(con)
    previo = {
        "fecha_inicio": catalogos.obtener_config(con, "fecha_inicio"),
        "ahorro_inicial": catalogos.obtener_config(con, "ahorro_inicial"),
        "saldos": {str(c["id"]): c["saldo_inicial"] for c in catalogos.listar_cuentas(con)},
    }
    rnd = random.Random(2026)
    limite = hoy()
    filas: list[tuple] = []

    def add(tipo, f, valor, desc, cuenta, cat=None):
        if f <= limite:
            filas.append((tipo, f, int(round(valor, -2)), desc, cu[cuenta], cat))

    g = lambda n: _cat(con, "gasto", n)  # noqa: E731
    i = lambda n: _cat(con, "ingreso", n)  # noqa: E731

    d = INICIO_DEMO
    while d <= limite:
        mes_factor = {9: 1.0, 10: 1.12, 11: 1.0}.get(d.month, 1.0)
        dia, finde = d.day, d.weekday() >= 5
        if dia == 15:
            add("ingreso", d, 2_100_000, "Salario primera quincena", "BBVA", i("Salario"))
        if dia == 30 or (d.month == 2 and dia == 28):
            add("ingreso", d, 2_100_000, "Salario segunda quincena", "BBVA", i("Salario"))
        if dia == 5:
            add("gasto", d, 1_300_000, "Arriendo apartamento", "Bancolombia", g("Arriendo"))
        if dia == 8:
            add("gasto", d, 95_000, "Internet hogar", "Bancolombia", g("Internet y telefonía"))
            add("gasto", d, 55_000, "Plan celular", "Nequi", g("Internet y telefonía"))
        if dia == 10:
            add("gasto", d, rnd.randint(105, 150) * 1000, "Recibo de luz", "Bancolombia", g("Servicios públicos"))
        if dia == 12:
            add("gasto", d, rnd.randint(60, 90) * 1000, "Recibo del agua", "Bancolombia", g("Servicios públicos"))
        if dia == 3:
            add("gasto", d, 26_900, "Netflix", "Nequi", g("Suscripciones"))
        if dia == 20:
            add("gasto", d, 16_900, "Spotify", "Nequi", g("Suscripciones"))
        if dia == 16:
            add("ahorro", d, 400_000, "Ahorro mensual", "Bancolombia")
        if dia == 28:
            add("gasto", d, rnd.randint(12, 18) * 1000, "4x1000", "Bancolombia", g("4x1000 (GMF)"))
        if d.weekday() == 5:
            add("gasto", d, rnd.randint(150, 280) * 1000, "Mercado semanal", "Nequi", g("Mercado"))
        if not finde and rnd.random() < 0.6:
            add("gasto", d, rnd.choice([14, 16, 18, 20, 22]) * 1000, "Almuerzo", "Nequi",
                g("Restaurantes y almuerzos"))
        if d.weekday() == 0:
            add("gasto", d, 50_000 * mes_factor, "Recarga transporte", "Nequi", g("Transporte público"))
        if dia in (2, 17):
            add("gasto", d, rnd.randint(70, 95) * 1000 * mes_factor, "Gasolina", "Bancolombia", g("Gasolina"))
        if d.month == 10 and dia == 22:
            add("gasto", d, 85_000, "Taxi aeropuerto", "Nequi", g("Taxi y apps"))
        if rnd.random() < 0.12:
            add("gasto", d, rnd.randint(30, 55) * 1000, "Domicilio Rappi", "Nequi", g("Domicilios"))
        if not finde and rnd.random() < 0.35:
            add("gasto", d, rnd.choice([4, 5, 6, 8, 9]) * 1000, "Café", "Efectivo", g("Café y snacks"))
        if finde and rnd.random() < 0.5:
            add("gasto", d, rnd.randint(40, 120) * 1000, "Salida con amigos", "Nequi", g("Salidas"))
        if rnd.random() < 0.05:
            add("gasto", d, rnd.randint(18, 60) * 1000, "Droguería", "Efectivo", g("Medicamentos"))
        if d == date(2026, 10, 24):
            add("ingreso", d, 600_000, "Proyecto freelance", "Nequi", i("Trabajos independientes"))
        if d == date(2026, 9, 19):
            add("gasto", d, 180_000, "Regalo cumpleaños", "Nequi", g("Regalos"))
        d += timedelta(days=1)

    with transaccion(con):
        catalogos.fijar_config(con, "demo_estado_previo", json.dumps(previo))
        catalogos.fijar_config(con, "fecha_inicio", INICIO_DEMO.isoformat())
        for nombre, saldo in SALDOS_DEMO.items():
            if nombre in cu:
                con.execute("UPDATE cuentas SET saldo_inicial = ? WHERE id = ?", (saldo, cu[nombre]))
        for tipo, f, valor, desc, cuenta_id, cat in filas:
            movimientos.crear(
                con,
                movimientos.DatosMovimiento(tipo=tipo, fecha=f.isoformat(), valor=valor, descripcion=desc,
                                            cuenta_id=cuenta_id, categoria_id=cat,
                                            observaciones="Dato de ejemplo (ficticio)"),
                origen="demo",
            )
    return len(filas)


def _restaurar_estado_previo(con: sqlite3.Connection) -> None:
    previo_txt = catalogos.obtener_config(con, "demo_estado_previo")
    if previo_txt is None:
        return
    previo = json.loads(previo_txt)
    catalogos.fijar_config(con, "fecha_inicio", previo["fecha_inicio"])
    catalogos.fijar_config(con, "ahorro_inicial", previo["ahorro_inicial"])
    for cuenta_id, saldo in previo["saldos"].items():
        con.execute("UPDATE cuentas SET saldo_inicial = ? WHERE id = ?", (saldo, int(cuenta_id)))
    con.execute("DELETE FROM configuracion WHERE clave = 'demo_estado_previo'")


def eliminar(con: sqlite3.Connection) -> int:
    """Borra por completo los movimientos de ejemplo y restaura la configuración previa."""
    if not demo_activo(con):
        raise ErrorValidacion("No hay datos de ejemplo cargados.")
    crear_respaldo(con, motivo="antes-de-eliminar-demo")
    with transaccion(con):
        con.execute(
            "DELETE FROM auditoria WHERE tabla = 'movimientos' AND registro_id IN "
            "(SELECT id FROM movimientos WHERE origen = 'demo')"
        )
        n = con.execute("DELETE FROM movimientos WHERE origen = 'demo'").rowcount
        _restaurar_estado_previo(con)
    return n


def reiniciar(con: sqlite3.Connection, fecha_inicio: str | None = None) -> dict:
    """Deja el sistema listo para datos reales: borra TODOS los movimientos e importaciones.

    Conserva cuentas, categorías, reglas y metas. Siempre crea un respaldo antes.
    """
    respaldo = crear_respaldo(con, motivo="antes-de-reiniciar")
    copias = [r["ruta_copia"] for r in con.execute("SELECT ruta_copia FROM lotes_importacion").fetchall()]
    with transaccion(con):
        _restaurar_estado_previo(con)
        n = con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0]
        con.execute("UPDATE configuracion SET valor = '1' WHERE clave = 'permitir_borrado_fisico'")
        con.execute("DELETE FROM lineas_importacion")
        # Los gastos fijos (plantillas) se conservan; sus meses registrados se olvidan.
        con.execute("DELETE FROM gastos_fijos_registros")
        con.execute("DELETE FROM movimientos")
        con.execute("DELETE FROM lotes_importacion")
        con.execute("DELETE FROM auditoria WHERE tabla = 'movimientos'")
        # Los pagos recibidos son operaciones como los movimientos; las deudas iniciales se conservan.
        con.execute("DELETE FROM abonos_prestamo")
        con.execute("DELETE FROM auditoria WHERE tabla = 'abonos_prestamo'")
        con.execute("DELETE FROM transferencias")
        con.execute("DELETE FROM auditoria WHERE tabla = 'transferencias'")
        con.execute("UPDATE configuracion SET valor = '0' WHERE clave = 'permitir_borrado_fisico'")
        if fecha_inicio:
            catalogos.fijar_config(con, "fecha_inicio", date.fromisoformat(fecha_inicio).isoformat())
    for ruta in copias:
        Path(ruta).unlink(missing_ok=True)
    return {"movimientos_borrados": n, "respaldo": respaldo}
