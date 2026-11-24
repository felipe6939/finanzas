"""«Me deben»: dinero prestado a otras personas y los pagos que te hacen.

Reglas:
- Lo que te deben NO es disponible (no está en tus cuentas) ni ingreso.
- Un pago recibido (abono) entra a una cuenta y reduce la deuda; no es ingreso.
- Nunca se abona más de lo pendiente. Nada se borra físicamente.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from ..core.fechas import hoy
from ..db.conexion import transaccion
from . import catalogos
from .errores import ErrorValidacion


def listar(con: sqlite3.Connection, incluir_saldados: bool = True) -> list[dict]:
    filas = con.execute("""
        SELECT p.*,
               COALESCE((SELECT SUM(a.valor) FROM abonos_prestamo a
                         WHERE a.prestamo_id = p.id AND a.eliminado_en IS NULL), 0) AS abonado
        FROM prestamos p
        WHERE p.eliminado_en IS NULL
        ORDER BY p.fecha IS NOT NULL, p.fecha, p.persona COLLATE NOCASE, p.id
    """).fetchall()
    res = []
    for f in filas:
        d = dict(f)
        d["pendiente"] = d["valor"] - d["abonado"]
        d["inicial"] = d["fecha"] is None
        d["abonos"] = abonos(con, d["id"])
        if incluir_saldados or d["pendiente"] > 0:
            res.append(d)
    return res


def abonos(con: sqlite3.Connection, prestamo_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT a.*, c.nombre AS cuenta FROM abonos_prestamo a JOIN cuentas c ON c.id = a.cuenta_id "
        "WHERE a.prestamo_id = ? AND a.eliminado_en IS NULL ORDER BY a.fecha, a.id",
        (prestamo_id,),
    ).fetchall()


def obtener(con: sqlite3.Connection, prestamo_id: int) -> dict | None:
    return next((p for p in listar(con) if p["id"] == prestamo_id), None)


def total_pendiente(con: sqlite3.Connection, hasta: date | None = None) -> int:
    """Lo que te deben a una fecha (deudas iniciales + préstamos hasta esa fecha − abonos)."""
    h = (hasta or date.max).isoformat()
    prestado = con.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM prestamos "
        "WHERE eliminado_en IS NULL AND (fecha IS NULL OR fecha <= ?)", (h,)
    ).fetchone()[0]
    return prestado - abonado_hasta(con, hasta)


def abonado_hasta(con: sqlite3.Connection, hasta: date | None = None) -> int:
    h = (hasta or date.max).isoformat()
    return con.execute(
        "SELECT COALESCE(SUM(a.valor), 0) FROM abonos_prestamo a JOIN prestamos p ON p.id = a.prestamo_id "
        "WHERE a.eliminado_en IS NULL AND p.eliminado_en IS NULL AND a.fecha <= ?", (h,)
    ).fetchone()[0]


# ----------------------------------------------------- deudas del punto de partida

def guardar_inicial(con: sqlite3.Connection, prestamo_id: int | None, persona: str,
                    valor: int | None, nota: str = "") -> int:
    """Crea o edita una deuda que ya existía al iniciar el sistema."""
    persona = (persona or "").strip()
    nota = (nota or "").strip() or None
    errores = []
    if not persona:
        errores.append("Indica quién te debe.")
    if valor is None or valor <= 0:
        errores.append("El valor que te deben debe ser mayor que cero.")
    actual = None
    if prestamo_id is not None:
        actual = obtener(con, prestamo_id)
        if not actual or not actual["inicial"]:
            errores.append("La deuda no existe.")
        elif valor is not None and valor < actual["abonado"]:
            errores.append(f"Ya te han pagado {actual['abonado']:,} de esta deuda; el valor no puede ser menor."
                           .replace(",", "."))
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        if actual is None:
            return con.execute(
                "INSERT INTO prestamos (persona, valor, nota) VALUES (?, ?, ?)", (persona, valor, nota)
            ).lastrowid
        con.execute("UPDATE prestamos SET persona = ?, valor = ?, nota = ? WHERE id = ?",
                    (persona, valor, nota, prestamo_id))
        return prestamo_id


def eliminar(con: sqlite3.Connection, prestamo_id: int) -> None:
    p = obtener(con, prestamo_id)
    if not p:
        raise ErrorValidacion("La deuda no existe.")
    if p["abonos"]:
        raise ErrorValidacion("Esta deuda tiene pagos registrados; elimina primero los pagos.")
    with transaccion(con):
        con.execute("UPDATE prestamos SET eliminado_en = strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime') "
                    "WHERE id = ?", (prestamo_id,))


# ------------------------------------------------------------------ pagos recibidos

def registrar_abono(con: sqlite3.Connection, prestamo_id: int, fecha: str, valor: int | None,
                    cuenta_id: int | None, nota: str = "") -> int:
    errores = []
    p = obtener(con, prestamo_id)
    if not p:
        raise ErrorValidacion("La deuda no existe.")
    try:
        f = date.fromisoformat(fecha or "")
    except ValueError:
        f = None
        errores.append("La fecha del pago no es válida.")
    if f is not None:
        if f > hoy():
            errores.append("La fecha del pago no puede ser futura.")
        if f < catalogos.fecha_inicio(con):
            errores.append("El pago es anterior a la fecha de inicio: ajusta el valor de la deuda en su lugar.")
        if p["fecha"] and f.isoformat() < p["fecha"]:
            errores.append("El pago no puede ser anterior al préstamo.")
    if valor is None or valor <= 0:
        errores.append("El valor del pago debe ser mayor que cero.")
    elif valor > p["pendiente"]:
        errores.append(f"El pago supera lo pendiente ({p['pendiente']:,}).".replace(",", "."))
    cuenta = catalogos.obtener_cuenta(con, cuenta_id) if cuenta_id else None
    if not cuenta or not cuenta["activa"]:
        errores.append("Elige una cuenta activa donde recibiste el pago.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        return con.execute(
            "INSERT INTO abonos_prestamo (prestamo_id, fecha, valor, cuenta_id, nota) VALUES (?, ?, ?, ?, ?)",
            (prestamo_id, f.isoformat(), valor, cuenta_id, (nota or "").strip() or None),
        ).lastrowid


def eliminar_abono(con: sqlite3.Connection, abono_id: int) -> None:
    with transaccion(con):
        n = con.execute(
            "UPDATE abonos_prestamo SET eliminado_en = strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime') "
            "WHERE id = ? AND eliminado_en IS NULL", (abono_id,)
        ).rowcount
    if not n:
        raise ErrorValidacion("El pago no existe.")
