"""Métricas financieras. Fuente única de cifras para el dashboard, los informes y la Skill.

Definiciones:
- Balance del mes   = ingresos − gastos.
- Ahorro del mes    = suma de movimientos de tipo 'ahorro' (dinero apartado a propósito).
- Sobrante          = ingresos − gastos − ahorro (lo que quedó sin destinar).
- Tasa de ahorro    = ahorro / ingresos.
- Patrimonio líquido a una fecha = saldos iniciales + ingresos − gastos (hasta esa fecha).
- Ahorro acumulado  = ahorro inicial + aportes de ahorro.
- Disponible        = patrimonio líquido − ahorro acumulado.

Comparaciones: si el mes consultado es el mes en curso, se compara contra el mes
anterior HASTA EL MISMO DÍA, para no comparar un mes parcial con uno completo.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..core.fechas import Mes, dia_equivalente, hoy, rango_meses
from ..core.formato import cop, porcentaje
from ..services import catalogos, prestamos


@dataclass
class Periodo:
    desde: date
    hasta: date
    etiqueta: str


def _ultima_fecha(con: sqlite3.Connection | None, mes: Mes) -> date | None:
    if con is None:
        return None
    f = con.execute(
        "SELECT MAX(fecha) FROM movimientos WHERE eliminado_en IS NULL AND fecha BETWEEN ? AND ?",
        (mes.inicio.isoformat(), mes.fin.isoformat()),
    ).fetchone()[0]
    return date.fromisoformat(f) if f else None


def periodos_comparables(mes: Mes, con: sqlite3.Connection | None = None) -> tuple[Periodo, Periodo, bool]:
    """(periodo actual, periodo de comparación, es_parcial).

    En el mes en curso el periodo va hasta hoy o hasta el último movimiento registrado
    (si hay programados con fecha futura), y se compara con el mes anterior hasta ese mismo día.
    """
    h = hoy()
    anterior = mes.anterior()
    if mes.contiene(h):
        h = max(h, _ultima_fecha(con, mes) or h)
        actual = Periodo(mes.inicio, h, f"1–{h.day} {mes.nombre[:3]}")
        fin_ant = dia_equivalente(anterior, h.day)
        previo = Periodo(anterior.inicio, fin_ant, f"1–{fin_ant.day} {anterior.nombre[:3]}")
        return actual, previo, True
    return (Periodo(mes.inicio, mes.fin, mes.etiqueta),
            Periodo(anterior.inicio, anterior.fin, anterior.etiqueta), False)


def _totales(con: sqlite3.Connection, desde: date, hasta: date) -> dict[str, int]:
    filas = con.execute(
        "SELECT tipo, COALESCE(SUM(valor), 0) AS total FROM movimientos "
        "WHERE eliminado_en IS NULL AND fecha BETWEEN ? AND ? GROUP BY tipo",
        (desde.isoformat(), hasta.isoformat()),
    ).fetchall()
    t = {"ingreso": 0, "gasto": 0, "ahorro": 0}
    t.update({f["tipo"]: f["total"] for f in filas if f["tipo"] in t})
    return t


def _variacion(actual: int, anterior: int) -> float | None:
    return None if anterior == 0 else (actual - anterior) / anterior


def posicion(con: sqlite3.Connection, hasta: date) -> dict[str, int]:
    """Patrimonio líquido, ahorro acumulado y disponible a una fecha."""
    t = con.execute(
        "SELECT tipo, COALESCE(SUM(valor), 0) AS total FROM movimientos "
        "WHERE eliminado_en IS NULL AND fecha <= ? GROUP BY tipo",
        (hasta.isoformat(),),
    ).fetchall()
    tot = {f["tipo"]: f["total"] for f in t}
    h = hasta.isoformat()
    # Préstamos hechos desde una cuenta salen del efectivo; los pagos recibidos vuelven a él.
    prestado = con.execute(
        "SELECT COALESCE(SUM(valor), 0) FROM prestamos "
        "WHERE eliminado_en IS NULL AND cuenta_id IS NOT NULL AND fecha <= ?", (h,)
    ).fetchone()[0]
    abonado = prestamos.abonado_hasta(con, hasta)
    patrimonio = (catalogos.saldo_inicial_total(con) + tot.get("ingreso", 0) - tot.get("gasto", 0)
                  - prestado + abonado)
    ahorro = catalogos.ahorro_inicial(con) + tot.get("ahorro", 0)
    return {"patrimonio": patrimonio, "ahorro_acumulado": ahorro, "disponible": patrimonio - ahorro,
            "me_deben": prestamos.total_pendiente(con, hasta)}


def kpis(con: sqlite3.Connection, mes: Mes) -> dict:
    actual_p, previo_p, parcial = periodos_comparables(mes, con)
    a = _totales(con, actual_p.desde, actual_p.hasta)
    p = _totales(con, previo_p.desde, previo_p.hasta)
    # Lo registrado mueve las cuentas desde que se registra, aunque tenga fecha futura:
    # en el mes en curso (o futuro) la posición incluye todo lo registrado.
    corte = mes.fin if mes.fin < hoy() else date.max
    pos = posicion(con, corte)
    programado = _totales(con, hoy() + timedelta(days=1), corte)

    def bloque(t: dict) -> dict:
        balance = t["ingreso"] - t["gasto"]
        return {
            "ingresos": t["ingreso"], "gastos": t["gasto"], "ahorro": t["ahorro"],
            "balance": balance, "sobrante": balance - t["ahorro"],
            "tasa_ahorro": (t["ahorro"] / t["ingreso"]) if t["ingreso"] else None,
        }

    act, prev = bloque(a), bloque(p)
    return {
        "actual": act,
        "anterior": prev,
        "variacion": {k: _variacion(act[k], prev[k]) for k in ("ingresos", "gastos", "ahorro")},
        "parcial": parcial,
        "periodo": actual_p,
        "periodo_comparacion": previo_p,
        "posicion": pos,
        "corte": corte,
        "programado": programado,
        "hay_programado": any(programado.values()),
        "hay_datos": any(a.values()),
        "hay_comparacion": any(p.values()),
    }


def gastos_por_categoria(con: sqlite3.Connection, mes: Mes) -> list[dict]:
    actual_p, previo_p, _ = periodos_comparables(mes, con)
    sql = """
        SELECT COALESCE(cp.id, c.id) AS id, COALESCE(cp.nombre, c.nombre) AS nombre, SUM(m.valor) AS total
        FROM movimientos m JOIN categorias c ON c.id = m.categoria_id
        LEFT JOIN categorias cp ON cp.id = c.padre_id
        WHERE m.eliminado_en IS NULL AND m.tipo = 'gasto' AND m.fecha BETWEEN ? AND ?
        GROUP BY 1, 2
    """
    actual = {f["id"]: dict(f) for f in con.execute(sql, (actual_p.desde.isoformat(), actual_p.hasta.isoformat()))}
    previo = {f["id"]: f["total"] for f in con.execute(sql, (previo_p.desde.isoformat(), previo_p.hasta.isoformat()))}
    total = sum(f["total"] for f in actual.values())
    filas = []
    for cid, f in actual.items():
        anterior = previo.get(cid, 0)
        filas.append({
            "id": cid, "nombre": f["nombre"], "total": f["total"],
            "participacion": f["total"] / total if total else 0,
            "anterior": anterior, "variacion": _variacion(f["total"], anterior),
        })
    return sorted(filas, key=lambda x: x["total"], reverse=True)


def subcategorias_de(con: sqlite3.Connection, mes: Mes, categoria_id: int) -> list[dict]:
    actual_p, _, _ = periodos_comparables(mes, con)
    filas = con.execute(
        """SELECT c.nombre, SUM(m.valor) AS total FROM movimientos m JOIN categorias c ON c.id = m.categoria_id
           WHERE m.eliminado_en IS NULL AND m.tipo = 'gasto' AND m.fecha BETWEEN ? AND ?
             AND (c.id = ? OR c.padre_id = ?) GROUP BY c.id ORDER BY total DESC""",
        (actual_p.desde.isoformat(), actual_p.hasta.isoformat(), categoria_id, categoria_id),
    ).fetchall()
    return [dict(f) for f in filas]


def distribucion(con: sqlite3.Connection, mes: Mes, campo: str) -> list[dict]:
    """Gastos del mes por 'naturaleza' (fijo/variable/extraordinario) o 'necesidad'."""
    if campo not in ("naturaleza", "necesidad"):
        raise ValueError(campo)
    orden = catalogos.NATURALEZAS if campo == "naturaleza" else catalogos.NECESIDADES
    actual_p, _, _ = periodos_comparables(mes, con)
    filas = con.execute(
        f"SELECT {campo} AS clave, SUM(valor) AS total FROM movimientos "
        "WHERE eliminado_en IS NULL AND tipo = 'gasto' AND fecha BETWEEN ? AND ? GROUP BY 1",
        (actual_p.desde.isoformat(), actual_p.hasta.isoformat()),
    ).fetchall()
    totales = {f["clave"]: f["total"] for f in filas}
    total = sum(totales.values())
    return [{"clave": k, "total": totales.get(k, 0), "participacion": (totales.get(k, 0) / total) if total else 0}
            for k in orden if totales.get(k, 0)]


def evolucion(con: sqlite3.Connection, hasta: Mes, meses: int = 14) -> list[dict]:
    """Ingresos, gastos y ahorro por mes: del mes de inicio a diciembre del año siguiente.

    Si `hasta` va más allá, la ventana se corre y muestra los últimos `meses`. Los meses
    futuros aparecen vacíos (None): no se inventan cifras.
    """
    inicio = Mes.de(catalogos.fecha_inicio(con))
    fin = max(Mes(inicio.anio + 1, 12), hasta)
    actual = Mes.de(hoy())
    resultado = []
    for m in rango_meses(fin, meses):
        if m < inicio:
            continue
        if actual < m and not _ultima_fecha(con, m):
            resultado.append({"mes": m.clave, "etiqueta": m.etiqueta_corta.capitalize(),
                              "ingresos": None, "gastos": None, "ahorro": None, "balance": None,
                              "parcial": False, "futuro": True})
            continue
        t = _totales(con, m.inicio, m.fin)
        resultado.append({
            "mes": m.clave, "etiqueta": m.etiqueta_corta.capitalize(),
            "ingresos": t["ingreso"], "gastos": t["gasto"], "ahorro": t["ahorro"],
            "balance": t["ingreso"] - t["gasto"],
            "parcial": m.contiene(hoy()), "futuro": actual < m,
        })
    return resultado


def ritmo_de_gasto(con: sqlite3.Connection, mes: Mes) -> dict:
    """Gasto acumulado día a día del mes y del mes anterior."""
    def acumulado(m: Mes, hasta_dia: int) -> list[int]:
        filas = con.execute(
            "SELECT CAST(strftime('%d', fecha) AS INTEGER) AS dia, SUM(valor) AS total FROM movimientos "
            "WHERE eliminado_en IS NULL AND tipo = 'gasto' AND fecha BETWEEN ? AND ? GROUP BY 1",
            (m.inicio.isoformat(), m.fin.isoformat()),
        ).fetchall()
        por_dia = {f["dia"]: f["total"] for f in filas}
        serie, suma = [], 0
        for d in range(1, hasta_dia + 1):
            suma += por_dia.get(d, 0)
            serie.append(suma)
        return serie

    h = hoy()
    ultimo = _ultima_fecha(con, mes)
    ultimo_dia = max(h.day, ultimo.day if ultimo else 0) if mes.contiene(h) else mes.dias
    anterior = mes.anterior()
    return {
        "dias": list(range(1, mes.dias + 1)),
        "actual": acumulado(mes, ultimo_dia) if mes.inicio <= h else [],
        "anterior": acumulado(anterior, anterior.dias)
                    if anterior >= Mes.de(catalogos.fecha_inicio(con)) else [],
        "etiqueta_actual": mes.etiqueta,
        "etiqueta_anterior": anterior.etiqueta,
    }


def insights(con: sqlite3.Connection, mes: Mes) -> list[dict]:
    """Observaciones con cifras reales. Cada una indica si es histórica o una proyección."""
    k = kpis(con, mes)
    if not k["hay_datos"]:
        return []
    obs: list[dict] = []
    act, prev = k["actual"], k["anterior"]
    comparacion = (f"el mismo periodo de {mes.anterior().nombre}" if k["parcial"]
                   else mes.anterior().nombre)
    nombre_mes = mes.nombre if not k["parcial"] else f"lo que va de {mes.nombre}"

    # En un mes en curso el balance parcial suele ser negativo (el arriendo se paga al
    # inicio y el salario llega después): solo se alerta con meses cerrados.
    if not k["parcial"] and act["ingresos"] and act["gastos"] > act["ingresos"]:
        obs.append({"clase": "historico", "nivel": "alerta", "texto":
                    f"En {nombre_mes} gastaste {cop(act['gastos'])}, {cop(act['gastos'] - act['ingresos'])} "
                    f"más de lo que recibiste ({cop(act['ingresos'])})."})

    categorias = gastos_por_categoria(con, mes)
    if categorias and act["gastos"]:
        top = categorias[0]
        obs.append({"clase": "historico", "nivel": "info", "texto":
                    f"{top['nombre']} es tu mayor gasto en {nombre_mes}: {cop(top['total'])}, "
                    f"el {porcentaje(top['participacion'])} del total."})

    if k["hay_comparacion"]:
        for c in categorias:
            v = c["variacion"]
            if v is None or c["participacion"] < 0.05 or abs(c["total"] - c["anterior"]) < 50_000:
                continue
            if v >= 0.15:
                obs.append({"clase": "historico", "nivel": "alerta", "texto":
                            f"Gastaste {porcentaje(v)} más en {c['nombre']} que en {comparacion} "
                            f"({cop(c['total'])} frente a {cop(c['anterior'])}). Representa el "
                            f"{porcentaje(c['participacion'])} de tus gastos."})
            elif v <= -0.15:
                obs.append({"clase": "historico", "nivel": "bueno", "texto":
                            f"Redujiste {porcentaje(-v)} el gasto en {c['nombre']} frente a {comparacion} "
                            f"({cop(c['total'])} frente a {cop(c['anterior'])})."})

        if act["tasa_ahorro"] is not None and prev["tasa_ahorro"] is not None:
            dif = act["tasa_ahorro"] - prev["tasa_ahorro"]
            if abs(dif) >= 0.03:
                obs.append({"clase": "historico", "nivel": "bueno" if dif > 0 else "alerta", "texto":
                            f"Tu tasa de ahorro {'subió' if dif > 0 else 'bajó'} de "
                            f"{porcentaje(prev['tasa_ahorro'])} a {porcentaje(act['tasa_ahorro'])} "
                            f"frente a {comparacion}."})

    disc = {d["clave"]: d for d in distribucion(con, mes, "necesidad")}
    if "discrecional" in disc and disc["discrecional"]["participacion"] >= 0.25:
        d = disc["discrecional"]
        obs.append({"clase": "historico", "nivel": "info", "texto":
                    f"Los gastos discrecionales suman {cop(d['total'])} ({porcentaje(d['participacion'])} "
                    f"de tus gastos): es el margen más directo si necesitas recortar."})

    # Proyección: solo para el mes en curso y con al menos 7 días transcurridos.
    # Solo se extrapolan los gastos VARIABLES; los fijos y extraordinarios no se repiten
    # cada día (el arriendo no se paga 30 veces).
    h = k["periodo"].hasta  # hoy, o el último movimiento programado del mes
    if k["parcial"] and h.day >= 7 and h.day < mes.dias:
        nat = {d["clave"]: d["total"] for d in distribucion(con, mes, "naturaleza")}
        variables = nat.get("variable", 0)
        no_variables = nat.get("fijo", 0) + nat.get("extraordinario", 0)
        if variables:
            por_dia = variables / h.day
            proyectado = no_variables + round(por_dia * mes.dias)
            texto = (f"Tus gastos variables van en {cop(variables)} ({cop(round(por_dia))} por día). "
                     f"A ese ritmo, cerrarías {mes.nombre} con gastos cercanos a {cop(proyectado)}")
            total_ant = _totales(con, mes.anterior().inicio, mes.anterior().fin)["gasto"]
            texto += f" (en {mes.anterior().nombre} completo fueron {cop(total_ant)})." if total_ant else "."
            obs.append({"clase": "proyeccion", "nivel": "info", "texto": texto,
                        "metodo": "Gastos fijos y extraordinarios registrados + promedio diario de los variables "
                                  "× días del mes. No incluye gastos fijos que aún no has registrado este mes."})
    return obs
