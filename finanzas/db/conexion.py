"""Conexión a SQLite y control explícito de transacciones."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import config


def conectar(ruta: Path | None = None) -> sqlite3.Connection:
    ruta = Path(ruta or config.ruta_db())
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: las transacciones se abren solo de forma explícita,
    # con transaccion(). Nada se confirma "a medias" de forma implícita.
    # check_same_thread=False: la web abre una conexión por petición y la usa en un
    # solo hilo a la vez (el de la petición); nunca se comparte entre peticiones.
    con = sqlite3.connect(ruta, isolation_level=None, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    return con


@contextmanager
def transaccion(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Todo o nada. Si ya hay una transacción abierta, se une a ella."""
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def ejecutar_script(con: sqlite3.Connection, sql: str) -> None:
    """Ejecuta un script sentencia por sentencia, sin confirmar la transacción.

    sqlite3.executescript() confirmaría la transacción en curso; esto no.
    """
    buffer = ""
    for linea in sql.splitlines(keepends=True):
        buffer += linea
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                con.execute(buffer)
            buffer = ""
    if buffer.strip():
        raise ValueError(f"Sentencia SQL incompleta: {buffer[:80]}")


def version_esquema(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]
