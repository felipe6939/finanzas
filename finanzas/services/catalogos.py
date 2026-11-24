"""Configuración, cuentas, categorías, metas de ahorro y reglas de clasificación."""
from __future__ import annotations

import sqlite3
from datetime import date

from ..core.texto import normalizar
from ..db.conexion import transaccion
from .errores import ErrorValidacion

NATURALEZAS = ("fijo", "variable", "extraordinario")
NECESIDADES = ("esencial", "discrecional")


# ---------------------------------------------------------------- configuración

def obtener_config(con: sqlite3.Connection, clave: str, defecto: str | None = None) -> str | None:
    fila = con.execute("SELECT valor FROM configuracion WHERE clave = ?", (clave,)).fetchone()
    return fila["valor"] if fila else defecto


def fijar_config(con: sqlite3.Connection, clave: str, valor: str) -> None:
    with transaccion(con):
        con.execute(
            "INSERT INTO configuracion (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor WHERE valor <> excluded.valor",
            (clave, str(valor)),
        )


def fecha_inicio(con: sqlite3.Connection) -> date:
    return date.fromisoformat(obtener_config(con, "fecha_inicio"))


def ahorro_inicial(con: sqlite3.Connection) -> int:
    return int(obtener_config(con, "ahorro_inicial", "0"))


def actualizar_inicio(con: sqlite3.Connection, fecha: str, ahorro: int) -> None:
    errores = []
    try:
        f = date.fromisoformat(fecha)
    except ValueError:
        errores.append("La fecha de inicio no es válida.")
        f = None
    if ahorro < 0:
        errores.append("El ahorro inicial no puede ser negativo.")
    if f is not None:
        anterior = con.execute(
            "SELECT MIN(fecha) AS f FROM movimientos WHERE eliminado_en IS NULL"
        ).fetchone()["f"]
        if anterior and anterior < f.isoformat():
            errores.append(
                f"Hay movimientos desde {anterior}; la fecha de inicio no puede ser posterior a ellos."
            )
        abono = con.execute(
            "SELECT MIN(fecha) AS f FROM (SELECT fecha FROM abonos_prestamo WHERE eliminado_en IS NULL "
            "UNION ALL SELECT fecha FROM transferencias WHERE eliminado_en IS NULL)"
        ).fetchone()["f"]
        if abono and abono < f.isoformat():
            errores.append(
                f"Hay pagos recibidos o movimientos entre cuentas desde {abono}; la fecha de inicio no puede ser posterior a ellos."
            )
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        fijar_config(con, "fecha_inicio", f.isoformat())
        fijar_config(con, "ahorro_inicial", str(ahorro))


# ---------------------------------------------------------------------- cuentas

def listar_cuentas(con: sqlite3.Connection, solo_activas: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM cuentas"
    if solo_activas:
        sql += " WHERE activa = 1"
    return con.execute(sql + " ORDER BY orden, id").fetchall()


def cuenta_por_defecto(con: sqlite3.Connection) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM cuentas WHERE por_defecto = 1 AND activa = 1").fetchone()


def obtener_cuenta(con: sqlite3.Connection, cuenta_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM cuentas WHERE id = ?", (cuenta_id,)).fetchone()


def _limpiar_alias(alias: str) -> str:
    partes = [normalizar(a) for a in alias.split(",")]
    return ",".join(p for p in partes if p)


def guardar_cuenta(con: sqlite3.Connection, cuenta_id: int | None, nombre: str, tipo: str,
                   alias: str, saldo_inicial: int, activa: bool = True) -> int:
    nombre = nombre.strip()
    errores = []
    if not nombre:
        errores.append("La cuenta necesita un nombre.")
    if tipo not in ("banco", "billetera", "efectivo"):
        errores.append("Tipo de cuenta no válido.")
    if saldo_inicial < 0:
        errores.append("El saldo inicial no puede ser negativo.")
    alias_limpio = _limpiar_alias(alias or nombre)
    # Un alias no puede pertenecer a dos cuentas: el lector no sabría cuál elegir.
    for otra in listar_cuentas(con):
        if otra["id"] == cuenta_id:
            continue
        repetidos = set(alias_limpio.split(",")) & set(otra["alias"].split(","))
        if repetidos - {""}:
            errores.append(f"El alias '{', '.join(sorted(repetidos))}' ya lo usa {otra['nombre']}.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        try:
            if cuenta_id is None:
                orden = con.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM cuentas").fetchone()[0]
                return con.execute(
                    "INSERT INTO cuentas (nombre, tipo, alias, saldo_inicial, activa, orden) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (nombre, tipo, alias_limpio, saldo_inicial, int(activa), orden),
                ).lastrowid
            if not activa:
                fila = obtener_cuenta(con, cuenta_id)
                if fila and fila["por_defecto"]:
                    raise ErrorValidacion("No se puede desactivar la cuenta por defecto.")
            con.execute(
                "UPDATE cuentas SET nombre = ?, tipo = ?, alias = ?, saldo_inicial = ?, activa = ? "
                "WHERE id = ?",
                (nombre, tipo, alias_limpio, saldo_inicial, int(activa), cuenta_id),
            )
            return cuenta_id
        except sqlite3.IntegrityError as e:
            raise ErrorValidacion(f"Ya existe una cuenta llamada {nombre}.") from e


def fijar_cuenta_por_defecto(con: sqlite3.Connection, cuenta_id: int) -> None:
    cuenta = obtener_cuenta(con, cuenta_id)
    if not cuenta or not cuenta["activa"]:
        raise ErrorValidacion("La cuenta no existe o está inactiva.")
    with transaccion(con):
        con.execute("UPDATE cuentas SET por_defecto = 0 WHERE por_defecto = 1 AND id <> ?", (cuenta_id,))
        con.execute("UPDATE cuentas SET por_defecto = 1 WHERE id = ? AND por_defecto = 0", (cuenta_id,))


def saldo_inicial_total(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COALESCE(SUM(saldo_inicial), 0) FROM cuentas").fetchone()[0]


# ------------------------------------------------------------------- categorías

def listar_categorias(con: sqlite3.Connection, tipo: str | None = None,
                      solo_activas: bool = False) -> list[dict]:
    """Lista plana con nombre completo ('Transporte › Gasolina'), padres primero."""
    sql = """
        SELECT c.*, p.nombre AS padre_nombre,
               COALESCE(c.naturaleza, p.naturaleza) AS naturaleza_efectiva,
               COALESCE(c.necesidad, p.necesidad) AS necesidad_efectiva
        FROM categorias c LEFT JOIN categorias p ON p.id = c.padre_id
        WHERE 1 = 1
    """
    params: list = []
    if tipo:
        sql += " AND c.tipo = ?"
        params.append(tipo)
    if solo_activas:
        sql += " AND c.activa = 1 AND (p.id IS NULL OR p.activa = 1)"
    sql += " ORDER BY c.tipo DESC, COALESCE(p.orden, c.orden), COALESCE(p.id, c.id), c.padre_id IS NOT NULL, c.orden, c.id"
    resultado = []
    for fila in con.execute(sql, params).fetchall():
        d = dict(fila)
        d["nombre_completo"] = f"{d['padre_nombre']} › {d['nombre']}" if d["padre_nombre"] else d["nombre"]
        resultado.append(d)
    return resultado


def arbol_categorias(con: sqlite3.Connection, tipo: str) -> list[dict]:
    planas = listar_categorias(con, tipo)
    padres = [dict(c, hijas=[]) for c in planas if c["padre_id"] is None]
    por_id = {p["id"]: p for p in padres}
    for c in planas:
        if c["padre_id"] is not None and c["padre_id"] in por_id:
            por_id[c["padre_id"]]["hijas"].append(c)
    return padres


def obtener_categoria(con: sqlite3.Connection, categoria_id: int) -> dict | None:
    for c in listar_categorias(con):
        if c["id"] == categoria_id:
            return c
    return None


def guardar_categoria(con: sqlite3.Connection, categoria_id: int | None, nombre: str, tipo: str,
                      padre_id: int | None, naturaleza: str | None, necesidad: str | None,
                      activa: bool = True) -> int:
    nombre = nombre.strip()
    errores = []
    if not nombre:
        errores.append("La categoría necesita un nombre.")
    if tipo not in ("gasto", "ingreso"):
        errores.append("Tipo de categoría no válido.")
    if naturaleza and naturaleza not in NATURALEZAS:
        errores.append("Naturaleza no válida.")
    if necesidad and necesidad not in NECESIDADES:
        errores.append("Necesidad no válida.")
    if padre_id is not None:
        padre = obtener_categoria(con, padre_id)
        if not padre or padre["padre_id"] is not None:
            errores.append("La categoría padre debe ser una categoría principal.")
        elif padre["tipo"] != tipo:
            errores.append("La subcategoría debe ser del mismo tipo que su categoría padre.")
        if categoria_id is not None and padre_id == categoria_id:
            errores.append("Una categoría no puede ser su propio padre.")
    if categoria_id is not None and padre_id is not None:
        tiene_hijas = con.execute(
            "SELECT 1 FROM categorias WHERE padre_id = ? LIMIT 1", (categoria_id,)
        ).fetchone()
        if tiene_hijas:
            errores.append("Una categoría con subcategorías no puede convertirse en subcategoría.")
    if categoria_id is not None:
        actual = obtener_categoria(con, categoria_id)
        if actual and actual["tipo"] != tipo:
            usada = con.execute(
                "SELECT 1 FROM movimientos WHERE categoria_id = ? LIMIT 1", (categoria_id,)
            ).fetchone()
            if usada:
                errores.append("No se puede cambiar el tipo de una categoría que ya tiene movimientos.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        try:
            if categoria_id is None:
                return con.execute(
                    "INSERT INTO categorias (nombre, padre_id, tipo, naturaleza, necesidad, activa, orden) "
                    "VALUES (?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(orden), 0) + 1 FROM categorias))",
                    (nombre, padre_id, tipo, naturaleza or None, necesidad or None, int(activa)),
                ).lastrowid
            con.execute(
                "UPDATE categorias SET nombre = ?, padre_id = ?, tipo = ?, naturaleza = ?, necesidad = ?, "
                "activa = ? WHERE id = ?",
                (nombre, padre_id, tipo, naturaleza or None, necesidad or None, int(activa), categoria_id),
            )
            return categoria_id
        except sqlite3.IntegrityError as e:
            raise ErrorValidacion(f"Ya existe la categoría '{nombre}' en ese nivel.") from e


def ids_categoria_y_hijas(con: sqlite3.Connection, categoria_id: int) -> list[int]:
    filas = con.execute(
        "SELECT id FROM categorias WHERE id = ? OR padre_id = ?", (categoria_id, categoria_id)
    ).fetchall()
    return [f["id"] for f in filas]


# ------------------------------------------------------------------------ metas

def listar_metas(con: sqlite3.Connection, solo_activas: bool = False) -> list[sqlite3.Row]:
    sql = """
        SELECT ma.*, COALESCE(SUM(m.valor), 0) AS acumulado
        FROM metas_ahorro ma
        LEFT JOIN movimientos m ON m.meta_id = ma.id AND m.tipo = 'ahorro' AND m.eliminado_en IS NULL
    """
    if solo_activas:
        sql += " WHERE ma.activa = 1"
    return con.execute(sql + " GROUP BY ma.id ORDER BY ma.activa DESC, ma.id").fetchall()


def guardar_meta(con: sqlite3.Connection, meta_id: int | None, nombre: str,
                 monto_objetivo: int | None, fecha_objetivo: str | None, activa: bool = True) -> int:
    nombre = nombre.strip()
    errores = []
    if not nombre:
        errores.append("La meta necesita un nombre.")
    if monto_objetivo is not None and monto_objetivo <= 0:
        errores.append("El monto objetivo debe ser mayor que cero.")
    if fecha_objetivo:
        try:
            date.fromisoformat(fecha_objetivo)
        except ValueError:
            errores.append("La fecha objetivo no es válida.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        try:
            if meta_id is None:
                return con.execute(
                    "INSERT INTO metas_ahorro (nombre, monto_objetivo, fecha_objetivo, activa) VALUES (?, ?, ?, ?)",
                    (nombre, monto_objetivo, fecha_objetivo or None, int(activa)),
                ).lastrowid
            con.execute(
                "UPDATE metas_ahorro SET nombre = ?, monto_objetivo = ?, fecha_objetivo = ?, activa = ? WHERE id = ?",
                (nombre, monto_objetivo, fecha_objetivo or None, int(activa), meta_id),
            )
            return meta_id
        except sqlite3.IntegrityError as e:
            raise ErrorValidacion(f"Ya existe una meta llamada {nombre}.") from e


# ----------------------------------------------------------------------- reglas

def listar_reglas(con: sqlite3.Connection) -> list[dict]:
    filas = con.execute(
        "SELECT r.* FROM reglas_clasificacion r ORDER BY r.origen DESC, r.tipo, r.patron"
    ).fetchall()
    categorias = {c["id"]: c for c in listar_categorias(con)}
    return [dict(r, categoria=categorias.get(r["categoria_id"], {}).get("nombre_completo", "?")) for r in filas]


def guardar_regla(con: sqlite3.Connection, patron: str, tipo: str, categoria_id: int) -> int:
    patron_n = normalizar(patron)
    errores = []
    if len(patron_n) < 2:
        errores.append("La palabra clave debe tener al menos 2 caracteres.")
    categoria = obtener_categoria(con, categoria_id)
    if not categoria or categoria["tipo"] != tipo:
        errores.append("La categoría no existe o no corresponde al tipo de movimiento.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        con.execute(
            "INSERT INTO reglas_clasificacion (patron, tipo, categoria_id, origen) VALUES (?, ?, ?, 'usuario') "
            "ON CONFLICT(patron, tipo) DO UPDATE SET categoria_id = excluded.categoria_id, origen = 'usuario'",
            (patron_n, tipo, categoria_id),
        )
        return con.execute(
            "SELECT id FROM reglas_clasificacion WHERE patron = ? AND tipo = ?", (patron_n, tipo)
        ).fetchone()["id"]


def eliminar_regla(con: sqlite3.Connection, regla_id: int) -> None:
    with transaccion(con):
        con.execute("DELETE FROM reglas_clasificacion WHERE id = ?", (regla_id,))
