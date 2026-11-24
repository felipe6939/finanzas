"""Registro, edición controlada y consulta de movimientos."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..core.fechas import hoy
from ..core.formato import cop, fecha_corta
from ..core.texto import normalizar
from ..db.conexion import transaccion
from . import catalogos
from .errores import ErrorValidacion

TIPOS = ("gasto", "ingreso", "ahorro")
VALOR_MAXIMO = 10_000_000_000
# Se pueden registrar movimientos con fecha futura (programados); cuentan desde que se registran.
DIAS_FUTURO_MAX = 366  # $ 10.000 millones: protección contra errores de digitación


@dataclass
class DatosMovimiento:
    tipo: str
    fecha: str
    valor: int | None
    descripcion: str
    cuenta_id: int | None
    categoria_id: int | None = None
    meta_id: int | None = None
    naturaleza: str | None = None
    necesidad: str | None = None
    observaciones: str = ""
    extras: dict = field(default_factory=dict)


def validar(con: sqlite3.Connection, d: DatosMovimiento) -> list[str]:
    errores: list[str] = []
    if d.tipo not in TIPOS:
        errores.append("El tipo debe ser gasto, ingreso o ahorro.")

    try:
        f = date.fromisoformat(d.fecha) if d.fecha else None
    except (TypeError, ValueError):
        f = None
    if f is None:
        errores.append("La fecha no es válida.")
    else:
        if f > hoy() + timedelta(days=DIAS_FUTURO_MAX):
            errores.append(f"La fecha {fecha_corta(f)} está a más de un año: ¿error de digitación?")
        inicio = catalogos.fecha_inicio(con)
        if f < inicio:
            errores.append(
                f"La fecha {fecha_corta(f)} es anterior al inicio del sistema ({fecha_corta(inicio)})."
            )

    if d.valor is None or not isinstance(d.valor, int) or isinstance(d.valor, bool):
        errores.append("El valor debe ser un número entero de pesos.")
    elif d.valor <= 0:
        errores.append("El valor debe ser mayor que cero.")
    elif d.valor > VALOR_MAXIMO:
        errores.append(f"El valor {cop(d.valor)} parece un error de digitación.")

    if not (d.descripcion or "").strip():
        errores.append("Falta la descripción.")
    elif len(d.descripcion) > 200:
        errores.append("La descripción es demasiado larga (máximo 200 caracteres).")

    cuenta = catalogos.obtener_cuenta(con, d.cuenta_id) if d.cuenta_id else None
    if not cuenta or not cuenta["activa"]:
        errores.append("La cuenta no existe o está inactiva.")

    if d.tipo in ("gasto", "ingreso"):
        cat = catalogos.obtener_categoria(con, d.categoria_id) if d.categoria_id else None
        if not cat:
            errores.append("Falta la categoría.")
        elif cat["tipo"] != d.tipo:
            errores.append(f"La categoría {cat['nombre_completo']} no es de tipo {d.tipo}.")
        elif not cat["activa"]:
            errores.append(f"La categoría {cat['nombre_completo']} está inactiva.")
        if d.meta_id:
            errores.append("Solo los movimientos de ahorro pueden tener una meta.")
    elif d.tipo == "ahorro":
        if d.categoria_id:
            errores.append("Un ahorro no lleva categoría de gasto o ingreso.")
        if d.meta_id:
            meta = con.execute("SELECT activa FROM metas_ahorro WHERE id = ?", (d.meta_id,)).fetchone()
            if not meta:
                errores.append("La meta de ahorro no existe.")

    if d.naturaleza and d.naturaleza not in catalogos.NATURALEZAS:
        errores.append("Naturaleza no válida.")
    if d.necesidad and d.necesidad not in catalogos.NECESIDADES:
        errores.append("Necesidad no válida.")
    return errores


def _resolver_clasificacion(con: sqlite3.Connection, d: DatosMovimiento) -> tuple[str | None, str | None]:
    """Si no se indican, la naturaleza y la necesidad se toman de la categoría.

    Se guardan en el movimiento: cambiar después los valores por defecto de una
    categoría no altera el historial.
    """
    if d.tipo == "ahorro":
        return None, None
    cat = catalogos.obtener_categoria(con, d.categoria_id)
    naturaleza = d.naturaleza or cat["naturaleza_efectiva"]
    necesidad = d.necesidad or (cat["necesidad_efectiva"] if d.tipo == "gasto" else None)
    return naturaleza, necesidad


def crear(con: sqlite3.Connection, d: DatosMovimiento, origen: str = "manual",
          lote_id: int | None = None, huella: str | None = None) -> int:
    errores = validar(con, d)
    if errores:
        raise ErrorValidacion(errores)
    naturaleza, necesidad = _resolver_clasificacion(con, d)
    with transaccion(con):
        return con.execute(
            """INSERT INTO movimientos (tipo, fecha, valor, descripcion, cuenta_id, categoria_id, meta_id,
                                        naturaleza, necesidad, observaciones, origen, lote_id, huella)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (d.tipo, d.fecha, d.valor, d.descripcion.strip(), d.cuenta_id,
             d.categoria_id if d.tipo != "ahorro" else None,
             d.meta_id if d.tipo == "ahorro" else None,
             naturaleza, necesidad, (d.observaciones or "").strip(), origen, lote_id, huella),
        ).lastrowid


def obtener(con: sqlite3.Connection, movimiento_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM movimientos WHERE id = ?", (movimiento_id,)).fetchone()


def actualizar(con: sqlite3.Connection, movimiento_id: int, d: DatosMovimiento) -> None:
    actual = obtener(con, movimiento_id)
    if not actual or actual["eliminado_en"]:
        raise ErrorValidacion("El movimiento no existe o fue eliminado.")
    errores = validar(con, d)
    if errores:
        raise ErrorValidacion(errores)
    naturaleza, necesidad = _resolver_clasificacion(con, d)
    with transaccion(con):
        con.execute(
            """UPDATE movimientos SET tipo = ?, fecha = ?, valor = ?, descripcion = ?, cuenta_id = ?,
                   categoria_id = ?, meta_id = ?, naturaleza = ?, necesidad = ?, observaciones = ?,
                   actualizado_en = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')
               WHERE id = ? AND eliminado_en IS NULL""",
            (d.tipo, d.fecha, d.valor, d.descripcion.strip(), d.cuenta_id,
             d.categoria_id if d.tipo != "ahorro" else None,
             d.meta_id if d.tipo == "ahorro" else None,
             naturaleza, necesidad, (d.observaciones or "").strip(), movimiento_id),
        )


def eliminar(con: sqlite3.Connection, movimiento_id: int) -> None:
    """Borrado lógico: el registro se conserva y la auditoría guarda el cambio."""
    with transaccion(con):
        cambiados = con.execute(
            "UPDATE movimientos SET eliminado_en = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime') "
            "WHERE id = ? AND eliminado_en IS NULL",
            (movimiento_id,),
        ).rowcount
    if not cambiados:
        raise ErrorValidacion("El movimiento no existe o ya estaba eliminado.")


def restaurar(con: sqlite3.Connection, movimiento_id: int) -> None:
    fila = obtener(con, movimiento_id)
    if not fila or not fila["eliminado_en"]:
        raise ErrorValidacion("El movimiento no está eliminado.")
    if fila["huella"]:
        choque = con.execute(
            "SELECT id FROM movimientos WHERE huella = ? AND eliminado_en IS NULL", (fila["huella"],)
        ).fetchone()
        if choque:
            raise ErrorValidacion(f"No se puede restaurar: el movimiento #{choque['id']} ya ocupa su lugar.")
    with transaccion(con):
        con.execute("UPDATE movimientos SET eliminado_en = NULL WHERE id = ?", (movimiento_id,))


CONSULTA_BASE = """
SELECT m.*, cu.nombre AS cuenta, c.nombre AS categoria, cp.nombre AS categoria_padre,
       COALESCE(cp.id, c.id) AS categoria_principal_id,
       COALESCE(cp.nombre, c.nombre) AS categoria_principal,
       ma.nombre AS meta
FROM movimientos m
JOIN cuentas cu ON cu.id = m.cuenta_id
LEFT JOIN categorias c ON c.id = m.categoria_id
LEFT JOIN categorias cp ON cp.id = c.padre_id
LEFT JOIN metas_ahorro ma ON ma.id = m.meta_id
"""


def listar(con: sqlite3.Connection, desde: date | None = None, hasta: date | None = None,
           tipo: str | None = None, categoria_id: int | None = None, cuenta_id: int | None = None,
           texto: str | None = None, naturaleza: str | None = None, necesidad: str | None = None,
           lote_id: int | None = None, incluir_eliminados: bool = False,
           limite: int | None = None) -> list[sqlite3.Row]:
    condiciones, params = [], []
    if not incluir_eliminados:
        condiciones.append("m.eliminado_en IS NULL")
    if desde:
        condiciones.append("m.fecha >= ?")
        params.append(desde.isoformat())
    if hasta:
        condiciones.append("m.fecha <= ?")
        params.append(hasta.isoformat())
    if tipo:
        condiciones.append("m.tipo = ?")
        params.append(tipo)
    if categoria_id:
        ids = catalogos.ids_categoria_y_hijas(con, categoria_id)
        condiciones.append(f"m.categoria_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if cuenta_id:
        condiciones.append("m.cuenta_id = ?")
        params.append(cuenta_id)
    if naturaleza:
        condiciones.append("m.naturaleza = ?")
        params.append(naturaleza)
    if necesidad:
        condiciones.append("m.necesidad = ?")
        params.append(necesidad)
    if lote_id:
        condiciones.append("m.lote_id = ?")
        params.append(lote_id)
    sql = CONSULTA_BASE
    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)
    sql += " ORDER BY m.fecha DESC, m.id DESC"
    filas = con.execute(sql, params).fetchall()
    if texto:
        buscado = normalizar(texto)
        filas = [f for f in filas if buscado in normalizar(f"{f['descripcion']} {f['observaciones']}")]
    return filas[:limite] if limite else filas


def posibles_duplicados(con: sqlite3.Connection, tipo: str, fecha: str, valor: int,
                        excluir: set[int] | None = None, dias: int = 1) -> list[sqlite3.Row]:
    """Movimientos existentes del mismo tipo y valor en fechas cercanas (±1 día)."""
    f = date.fromisoformat(fecha)
    filas = con.execute(
        CONSULTA_BASE + " WHERE m.eliminado_en IS NULL AND m.tipo = ? AND m.valor = ? "
        "AND m.fecha BETWEEN ? AND ? ORDER BY m.fecha",
        (tipo, valor, (f - timedelta(days=dias)).isoformat(), (f + timedelta(days=dias)).isoformat()),
    ).fetchall()
    excluir = excluir or set()
    return [r for r in filas if r["id"] not in excluir]


def historial(con: sqlite3.Connection, movimiento_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM auditoria WHERE tabla = 'movimientos' AND registro_id = ? ORDER BY id",
        (movimiento_id,),
    ).fetchall()
