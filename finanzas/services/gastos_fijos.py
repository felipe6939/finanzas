"""Gastos fijos mensuales: plantillas (arriendo, servicios...) que se registran con un clic.

Reglas:
- Al marcar la casilla de un gasto fijo se registra en ese momento como un movimiento
  normal: su categoría, su cuenta y la fecha del día en que se marca.
- Una vez por mes: si ese mes ya está registrado, no deja registrarlo de nuevo
  (tabla gastos_fijos_registros, UNIQUE por gasto y mes).
- Si ese mes ya hay un gasto de la misma categoría y el mismo valor (p. ej. registrado a
  mano), se toma ese como el del mes y no se crea otro.
- «Deshacer» elimina el movimiento del mes (borrado lógico, queda en el historial) y
  vuelve a dejar la casilla disponible. Lo mismo si eliminas el movimiento a mano.
- Nada se registra solo. Las columnas `dia`, `automatico` y `desde_mes` de la tabla
  quedaron de la primera versión y no se usan.
"""
from __future__ import annotations

import sqlite3

from ..core.fechas import Mes, hoy
from ..core.formato import cop, fecha_corta
from ..db.conexion import transaccion
from . import catalogos, movimientos
from .errores import ErrorValidacion

NOTA = "Gasto fijo mensual"


def listar(con: sqlite3.Connection) -> list[dict]:
    filas = con.execute("""
        SELECT g.*, cu.nombre AS cuenta, c.nombre AS categoria, cp.nombre AS categoria_padre
        FROM gastos_fijos g
        JOIN cuentas cu ON cu.id = g.cuenta_id
        JOIN categorias c ON c.id = g.categoria_id
        LEFT JOIN categorias cp ON cp.id = c.padre_id
        WHERE g.eliminado_en IS NULL
        ORDER BY g.descripcion COLLATE NOCASE, g.id
    """).fetchall()
    mes = Mes.de(hoy())
    res = []
    for f in filas:
        d = dict(f)
        d["categoria_completa"] = f"{f['categoria_padre']} › {f['categoria']}" if f["categoria_padre"] else f["categoria"]
        d["este_mes"] = registro_del_mes(con, f["id"], mes)
        res.append(d)
    return res


def obtener(con: sqlite3.Connection, gasto_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM gastos_fijos WHERE id = ? AND eliminado_en IS NULL", (gasto_id,)).fetchone()


def registro_del_mes(con: sqlite3.Connection, gasto_id: int, mes: Mes) -> dict | None:
    """El movimiento con el que quedó registrado ese mes, si sigue vigente (no eliminado)."""
    fila = con.execute(
        "SELECT r.id AS registro_id, m.id AS movimiento_id, m.fecha, m.valor, cu.nombre AS cuenta "
        "FROM gastos_fijos_registros r JOIN movimientos m ON m.id = r.movimiento_id "
        "JOIN cuentas cu ON cu.id = m.cuenta_id "
        "WHERE r.gasto_fijo_id = ? AND r.mes = ? AND m.eliminado_en IS NULL",
        (gasto_id, mes.clave),
    ).fetchone()
    return dict(fila) if fila else None


def _validar(con: sqlite3.Connection, descripcion: str, valor: int | None, categoria_id: int | None,
             cuenta_id: int | None) -> list[str]:
    errores = []
    if not (descripcion or "").strip():
        errores.append("Falta la descripción.")
    elif len(descripcion) > 200:
        errores.append("La descripción es demasiado larga (máximo 200 caracteres).")
    if valor is None or valor <= 0:
        errores.append("El valor debe ser mayor que cero.")
    elif valor > movimientos.VALOR_MAXIMO:
        errores.append(f"El valor {cop(valor)} parece un error de digitación.")
    cat = catalogos.obtener_categoria(con, categoria_id) if categoria_id else None
    if not cat:
        errores.append("Falta la categoría.")
    elif cat["tipo"] != "gasto":
        errores.append("La categoría debe ser de gasto.")
    elif not cat["activa"]:
        errores.append(f"La categoría {cat['nombre_completo']} está inactiva.")
    cuenta = catalogos.obtener_cuenta(con, cuenta_id) if cuenta_id else None
    if not cuenta or not cuenta["activa"]:
        errores.append("La cuenta no existe o está inactiva.")
    return errores


def guardar(con: sqlite3.Connection, gasto_id: int | None, descripcion: str, valor: int | None,
            categoria_id: int | None, cuenta_id: int | None) -> int:
    """Crea o edita la plantilla. Editarla no cambia lo que ya se registró."""
    errores = _validar(con, descripcion, valor, categoria_id, cuenta_id)
    if gasto_id is not None and not obtener(con, gasto_id):
        errores.append("El gasto fijo no existe.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        if gasto_id is None:
            return con.execute(
                "INSERT INTO gastos_fijos (descripcion, valor, categoria_id, cuenta_id, dia) VALUES (?, ?, ?, ?, 1)",
                (descripcion.strip(), valor, categoria_id, cuenta_id),
            ).lastrowid
        con.execute(
            "UPDATE gastos_fijos SET descripcion = ?, valor = ?, categoria_id = ?, cuenta_id = ? WHERE id = ?",
            (descripcion.strip(), valor, categoria_id, cuenta_id, gasto_id))
        return gasto_id


def eliminar(con: sqlite3.Connection, gasto_id: int) -> None:
    """Borrado lógico de la plantilla. Los gastos ya registrados se conservan."""
    with transaccion(con):
        n = con.execute(
            "UPDATE gastos_fijos SET eliminado_en = strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime'), "
            "automatico = 0 WHERE id = ? AND eliminado_en IS NULL", (gasto_id,)).rowcount
    if not n:
        raise ErrorValidacion("El gasto fijo no existe o ya fue eliminado.")


def _existente(con: sqlite3.Connection, g: sqlite3.Row, mes: Mes) -> sqlite3.Row | None:
    """Un gasto ya registrado ese mes con la misma categoría y valor, sin vincular a otro gasto fijo."""
    return con.execute(
        "SELECT * FROM movimientos m WHERE m.eliminado_en IS NULL AND m.tipo = 'gasto' "
        "AND m.categoria_id = ? AND m.valor = ? AND m.fecha BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM gastos_fijos_registros r WHERE r.movimiento_id = m.id) "
        "ORDER BY m.fecha, m.id LIMIT 1",
        (g["categoria_id"], g["valor"], mes.inicio.isoformat(), mes.fin.isoformat()),
    ).fetchone()


def registrar_mes(con: sqlite3.Connection, gasto_id: int) -> str:
    """Registra el gasto fijo HOY, en su categoría y cuenta. Una sola vez por mes."""
    g = obtener(con, gasto_id)
    if not g:
        raise ErrorValidacion("El gasto fijo no existe.")
    hoy_ = hoy()
    mes = Mes.de(hoy_)
    with transaccion(con):
        ya = registro_del_mes(con, gasto_id, mes)
        if ya:
            raise ErrorValidacion(
                f"{g['descripcion']} ya está registrado este mes ({fecha_corta(ya['fecha'])}). "
                "No se puede registrar dos veces en el mismo mes.")
        previo = _existente(con, g, mes)
        if previo:
            mov_id = previo["id"]
            msg = (f"{g['descripcion']} ya estaba registrado este mes: «{previo['descripcion']}» "
                   f"{cop(previo['valor'])} el {fecha_corta(previo['fecha'])}. Quedó marcado y no se duplicó.")
        else:
            errores = _validar(con, g["descripcion"], g["valor"], g["categoria_id"], g["cuenta_id"])
            if errores:
                raise ErrorValidacion(errores)
            mov_id = movimientos.crear(con, movimientos.DatosMovimiento(
                tipo="gasto", fecha=hoy_.isoformat(), valor=g["valor"], descripcion=g["descripcion"],
                cuenta_id=g["cuenta_id"], categoria_id=g["categoria_id"], observaciones=NOTA))
            cuenta = catalogos.obtener_cuenta(con, g["cuenta_id"])
            msg = (f"Registrado: {g['descripcion']} {cop(g['valor'])} · {cuenta['nombre']} · "
                   f"{fecha_corta(hoy_)}.")
        # Si el registro del mes existía pero su movimiento fue eliminado, se reutiliza.
        con.execute(
            "INSERT INTO gastos_fijos_registros (gasto_fijo_id, mes, movimiento_id) VALUES (?, ?, ?) "
            "ON CONFLICT (gasto_fijo_id, mes) DO UPDATE SET movimiento_id = excluded.movimiento_id",
            (gasto_id, mes.clave, mov_id))
    return msg


def deshacer_mes(con: sqlite3.Connection, gasto_id: int) -> str:
    """Elimina (lógicamente) el movimiento del mes en curso y libera la casilla."""
    g = obtener(con, gasto_id)
    ya = registro_del_mes(con, gasto_id, Mes.de(hoy())) if g else None
    if not ya:
        raise ErrorValidacion("Este gasto fijo no está registrado este mes.")
    movimientos.eliminar(con, ya["movimiento_id"])
    return f"Se deshizo el registro de {g['descripcion']} de este mes (queda en el historial)."
