"""Saldo de cada cuenta y movimientos de dinero entre cuentas propias.

Saldo de una cuenta = saldo inicial + ingresos − gastos − préstamos hechos desde ella
                      + pagos recibidos en ella − enviado a otras cuentas + recibido de otras.
El ahorro (tipo «ahorro») no saca dinero de la cuenta: lo marca como apartado.
Todo lo registrado cuenta, también lo programado con fecha futura.
`saldos` da el saldo con todos los meses; `saldos_mes`, cómo se movió cada cuenta en un mes.
La suma de los saldos es igual al patrimonio líquido de `resumen.posicion`.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from ..core.fechas import Mes, hoy
from ..db.conexion import transaccion
from . import catalogos
from .errores import ErrorValidacion
from .movimientos import DIAS_FUTURO_MAX


def _rango(columna: str, desde: date | None, hasta: date | None) -> tuple[str, tuple]:
    cond, params = [], []
    if desde is not None:
        cond.append(f"{columna} >= ?")
        params.append(desde.isoformat())
    if hasta is not None:
        cond.append(f"{columna} <= ?")
        params.append(hasta.isoformat())
    return "".join(f" AND {c}" for c in cond), tuple(params)


def flujos(con: sqlite3.Connection, desde: date | None = None, hasta: date | None = None) -> dict[int, dict]:
    """Entradas y salidas de dinero de cada cuenta entre dos fechas (incluidas; None = sin límite)."""
    extra, params = _rango("fecha", desde, hasta)
    res: dict[int, dict] = {}

    def de(cuenta_id: int) -> dict:
        return res.setdefault(cuenta_id, dict.fromkeys(
            ("ingresos", "gastos", "ahorro", "prestado", "abonos", "enviado", "recibido"), 0) | {"ultima": None})

    for f in con.execute("SELECT cuenta_id, tipo, SUM(valor), MAX(fecha) FROM movimientos "
                         f"WHERE eliminado_en IS NULL{extra} GROUP BY cuenta_id, tipo", params).fetchall():
        d = de(f[0])
        clave = {"ingreso": "ingresos", "gasto": "gastos", "ahorro": "ahorro"}.get(f[1])
        if clave:
            d[clave] += f[2]
        d["ultima"] = max(filter(None, (d["ultima"], f[3])), default=None)
    consultas = {
        "prestado": ("SELECT cuenta_id, SUM(valor), MAX(fecha) FROM prestamos WHERE eliminado_en IS NULL "
                     "AND cuenta_id IS NOT NULL{rango} GROUP BY cuenta_id", "fecha"),
        "abonos": ("SELECT a.cuenta_id, SUM(a.valor), MAX(a.fecha) FROM abonos_prestamo a "
                   "JOIN prestamos p ON p.id = a.prestamo_id WHERE a.eliminado_en IS NULL "
                   "AND p.eliminado_en IS NULL{rango} GROUP BY a.cuenta_id", "a.fecha"),
        "enviado": ("SELECT cuenta_origen_id, SUM(valor), MAX(fecha) FROM transferencias "
                    "WHERE eliminado_en IS NULL{rango} GROUP BY cuenta_origen_id", "fecha"),
        "recibido": ("SELECT cuenta_destino_id, SUM(valor), MAX(fecha) FROM transferencias "
                     "WHERE eliminado_en IS NULL{rango} GROUP BY cuenta_destino_id", "fecha"),
    }
    for clave, (sql, columna) in consultas.items():
        extra, params = _rango(columna, desde, hasta)
        for f in con.execute(sql.format(rango=extra), params).fetchall():
            d = de(f[0])
            d[clave] += f[1]
            d["ultima"] = max(filter(None, (d["ultima"], f[2])), default=None)
    for d in res.values():
        d["entradas"] = d["ingresos"] + d["abonos"] + d["recibido"]
        d["salidas"] = d["gastos"] + d["prestado"] + d["enviado"]
        d["neto"] = d["entradas"] - d["salidas"]
    return res


_VACIO = {"ingresos": 0, "gastos": 0, "ahorro": 0, "prestado": 0, "abonos": 0, "enviado": 0,
          "recibido": 0, "entradas": 0, "salidas": 0, "neto": 0, "ultima": None}


def saldos(con: sqlite3.Connection) -> list[dict]:
    """Saldo actual de cada cuenta con todo lo registrado (de todos los meses)."""
    todo = flujos(con)
    futuro = flujos(con, hoy() + timedelta(days=1))
    res = []
    for c in catalogos.listar_cuentas(con):
        f = todo.get(c["id"], _VACIO)
        res.append({
            "id": c["id"], "nombre": c["nombre"], "tipo": c["tipo"], "activa": bool(c["activa"]),
            "por_defecto": bool(c["por_defecto"]), "saldo": c["saldo_inicial"] + f["neto"],
            "programado": futuro.get(c["id"], _VACIO)["neto"],
            "ahorro": f["ahorro"], "ultima": f["ultima"],
            "ingresos": f["ingresos"], "gastos": f["gastos"],
        })
    return res


def saldos_mes(con: sqlite3.Connection, mes: Mes) -> list[dict]:
    """Cómo se movió cada cuenta en un mes: saldo al empezar, entradas, salidas y saldo al final.

    El saldo al final incluye lo programado con fecha dentro del mes.
    """
    antes = flujos(con, None, mes.inicio - timedelta(days=1))
    del_mes = flujos(con, mes.inicio, mes.fin)
    manana = hoy() + timedelta(days=1)
    futuro = flujos(con, max(manana, mes.inicio), mes.fin) if manana <= mes.fin else {}
    res = []
    for c in catalogos.listar_cuentas(con):
        inicio = c["saldo_inicial"] + antes.get(c["id"], _VACIO)["neto"]
        f = del_mes.get(c["id"], _VACIO)
        res.append({
            "id": c["id"], "nombre": c["nombre"], "tipo": c["tipo"], "activa": bool(c["activa"]),
            "por_defecto": bool(c["por_defecto"]), "inicio": inicio, "final": inicio + f["neto"],
            "programado": futuro.get(c["id"], _VACIO)["neto"],
            **{k: f[k] for k in ("ingresos", "gastos", "ahorro", "prestado", "abonos", "enviado",
                                 "recibido", "entradas", "salidas", "neto", "ultima")},
        })
    return res


# -------------------------------------------------------------- entre cuentas

def transferir(con: sqlite3.Connection, fecha: str, valor: int | None, origen_id: int | None,
               destino_id: int | None, nota: str = "") -> int:
    errores = []
    try:
        f = date.fromisoformat(fecha or "")
    except ValueError:
        f = None
        errores.append("La fecha no es válida.")
    if f is not None:
        if f < catalogos.fecha_inicio(con):
            errores.append("La fecha es anterior al inicio del sistema.")
        if f > hoy() + timedelta(days=DIAS_FUTURO_MAX):
            errores.append("La fecha está a más de un año: ¿error de digitación?")
    if valor is None or valor <= 0:
        errores.append("El valor debe ser mayor que cero.")
    origen = catalogos.obtener_cuenta(con, origen_id) if origen_id else None
    destino = catalogos.obtener_cuenta(con, destino_id) if destino_id else None
    if not origen or not origen["activa"] or not destino or not destino["activa"]:
        errores.append("Elige dos cuentas activas.")
    elif origen_id == destino_id:
        errores.append("La cuenta de origen y la de destino deben ser distintas.")
    if errores:
        raise ErrorValidacion(errores)
    with transaccion(con):
        return con.execute(
            "INSERT INTO transferencias (fecha, valor, cuenta_origen_id, cuenta_destino_id, nota) "
            "VALUES (?, ?, ?, ?, ?)", (f.isoformat(), valor, origen_id, destino_id, (nota or "").strip() or None),
        ).lastrowid


def listar_transferencias(con: sqlite3.Connection, limite: int = 15) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT t.*, o.nombre AS origen, d.nombre AS destino FROM transferencias t "
        "JOIN cuentas o ON o.id = t.cuenta_origen_id JOIN cuentas d ON d.id = t.cuenta_destino_id "
        "WHERE t.eliminado_en IS NULL ORDER BY t.fecha DESC, t.id DESC LIMIT ?", (limite,)
    ).fetchall()


def eliminar_transferencia(con: sqlite3.Connection, transferencia_id: int) -> None:
    with transaccion(con):
        n = con.execute(
            "UPDATE transferencias SET eliminado_en = strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime') "
            "WHERE id = ? AND eliminado_en IS NULL", (transferencia_id,)
        ).rowcount
    if not n:
        raise ErrorValidacion("El movimiento entre cuentas no existe.")
