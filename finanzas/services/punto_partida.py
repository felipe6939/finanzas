"""Registrar el punto de partida como ingresos del primer mes (operación única).

Los saldos iniciales de cada cuenta pasan a ser movimientos de ingreso en la fecha
de inicio (categoría «Punto de partida») y el saldo inicial de la cuenta queda en 0.
El disponible y el patrimonio no cambian: solo cambia cómo se presenta el dinero
con el que empezaste (como ingreso del primer mes en lugar de saldo previo).
"""
from __future__ import annotations

import sqlite3

from ..db.conexion import transaccion
from . import catalogos, movimientos
from .errores import ErrorValidacion

CLAVE = "punto_partida_en_ingresos"
CATEGORIA = "Punto de partida"


def ya_aplicado(con: sqlite3.Connection) -> bool:
    return catalogos.obtener_config(con, CLAVE) is not None


def total(con: sqlite3.Connection) -> int:
    """Suma de los ingresos «Punto de partida» vigentes."""
    return con.execute(
        "SELECT COALESCE(SUM(m.valor), 0) FROM movimientos m JOIN categorias c ON c.id = m.categoria_id "
        "WHERE m.eliminado_en IS NULL AND m.tipo = 'ingreso' AND c.padre_id IS NULL AND c.nombre = ?",
        (CATEGORIA,),
    ).fetchone()[0]


def _categoria(con: sqlite3.Connection) -> int:
    for c in catalogos.listar_categorias(con, "ingreso"):
        if c["padre_id"] is None and c["nombre"].lower() == CATEGORIA.lower():
            return c["id"]
    return catalogos.guardar_categoria(con, None, CATEGORIA, "ingreso", None, "extraordinario", None)


def registrar_como_ingresos(con: sqlite3.Connection) -> list[dict]:
    if ya_aplicado(con):
        raise ErrorValidacion("El punto de partida ya se registró como ingresos.")
    cuentas = [c for c in catalogos.listar_cuentas(con) if c["saldo_inicial"] > 0]
    if not cuentas:
        raise ErrorValidacion("No hay saldos iniciales para registrar.")
    from ..respaldo import crear_respaldo
    crear_respaldo(con, motivo="antes-de-punto-de-partida-en-ingresos")
    fecha = catalogos.fecha_inicio(con)
    hechos = []
    with transaccion(con):
        cat = _categoria(con)
        for c in cuentas:
            movimientos.crear(con, movimientos.DatosMovimiento(
                tipo="ingreso", fecha=fecha.isoformat(), valor=c["saldo_inicial"],
                descripcion=f"Punto de partida · saldo de {c['nombre']}",
                cuenta_id=c["id"], categoria_id=cat,
                observaciones="Saldo con el que empezó el sistema, registrado como ingreso del primer mes.",
            ), origen="manual")
            catalogos.guardar_cuenta(con, c["id"], c["nombre"], c["tipo"], c["alias"], 0, bool(c["activa"]))
            hechos.append({"cuenta": c["nombre"], "valor": c["saldo_inicial"]})
        catalogos.fijar_config(con, CLAVE, fecha.isoformat())
    return hechos
