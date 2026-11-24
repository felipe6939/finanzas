"""Migraciones versionadas del esquema (PRAGMA user_version).

Cada migración se aplica dentro de una transacción: o se aplica completa o no
se aplica. Antes de migrar una base con datos, se crea un respaldo.
"""
from __future__ import annotations

import sqlite3

from .conexion import ejecutar_script, transaccion, version_esquema
from .migraciones import m001_esquema, m002_datos_base, m003_me_deben, m004_transferencias, m005_gastos_fijos

MIGRACIONES = [
    (1, m001_esquema.aplicar),
    (2, m002_datos_base.aplicar),
    (3, m003_me_deben.aplicar),
    (4, m004_transferencias.aplicar),
    (5, m005_gastos_fijos.aplicar),
]

VERSION_ACTUAL = MIGRACIONES[-1][0]


def migrar(con: sqlite3.Connection, respaldar: bool = True) -> list[int]:
    actual = version_esquema(con)
    if actual > VERSION_ACTUAL:
        raise RuntimeError(
            f"La base de datos (v{actual}) es más nueva que el código (v{VERSION_ACTUAL})."
        )
    pendientes = [(n, fn) for n, fn in MIGRACIONES if n > actual]
    if pendientes and actual > 0 and respaldar:
        from ..respaldo import crear_respaldo
        crear_respaldo(con, motivo=f"antes-de-migrar-v{actual}")
    aplicadas = []
    for numero, aplicar in pendientes:
        with transaccion(con):
            aplicar(con, ejecutar_script)
            con.execute(f"PRAGMA user_version = {numero}")
        aplicadas.append(numero)
    return aplicadas
