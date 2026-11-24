"""Datos del reporte PDF: una foto de las finanzas con corte a una fecha (por defecto, hoy).

Todas las cifras van HASTA la fecha de corte, incluida. Lo registrado con fecha posterior
(programados) NO se suma en las cifras a hoy: se lista en «Lo que viene» y su efecto se
muestra en «Cómo quedarías con lo programado» (saldos y disponible sumándolo).
Las definiciones (patrimonio, disponible, ahorro) son las de `resumen`.
Las observaciones solo describen cifras registradas; no hay proyecciones.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from ..core.fechas import Mes, dia_equivalente, hoy
from ..core.formato import MESES, cop, fecha_corta, porcentaje
from ..services import catalogos, prestamos, saldos
from . import resumen

CATEGORIA_PUNTO_PARTIDA = "Punto de partida"


def _bloque(t: dict) -> dict:
    balance = t["ingreso"] - t["gasto"]
    return {"ingresos": t["ingreso"], "gastos": t["gasto"], "ahorro": t["ahorro"], "balance": balance,
            "tasa_ahorro": (t["ahorro"] / t["ingreso"]) if t["ingreso"] else None}


def _por_categoria(con: sqlite3.Connection, tipo: str, desde: date, hasta: date) -> list[dict]:
    """Totales por categoría principal (las subcategorías se suman a su categoría)."""
    filas = con.execute(
        """SELECT COALESCE(cp.nombre, c.nombre, 'Sin categoría') AS nombre, SUM(m.valor) AS total
           FROM movimientos m LEFT JOIN categorias c ON c.id = m.categoria_id
           LEFT JOIN categorias cp ON cp.id = c.padre_id
           WHERE m.eliminado_en IS NULL AND m.tipo = ? AND m.fecha BETWEEN ? AND ?
           GROUP BY 1 ORDER BY total DESC, nombre""",
        (tipo, desde.isoformat(), hasta.isoformat()),
    ).fetchall()
    total = sum(f["total"] for f in filas)
    return [{"nombre": f["nombre"], "total": f["total"], "participacion": f["total"] / total if total else 0}
            for f in filas]


def _por_campo(con: sqlite3.Connection, campo: str, desde: date, hasta: date) -> list[dict]:
    orden = catalogos.NATURALEZAS if campo == "naturaleza" else catalogos.NECESIDADES
    filas = dict(con.execute(
        f"SELECT {campo}, SUM(valor) FROM movimientos WHERE eliminado_en IS NULL AND tipo = 'gasto' "
        "AND fecha BETWEEN ? AND ? GROUP BY 1", (desde.isoformat(), hasta.isoformat()),
    ).fetchall())
    total = sum(filas.values())
    res = [{"clave": k, "total": filas.get(k, 0), "participacion": filas.get(k, 0) / total if total else 0}
           for k in orden if filas.get(k)]
    sin = filas.get(None, 0)
    if sin:
        res.append({"clave": "sin clasificar", "total": sin, "participacion": sin / total})
    return res


def _movimientos(con: sqlite3.Connection, where: str, params: tuple, orden: str, limite: int) -> list[dict]:
    filas = con.execute(
        f"""SELECT m.fecha, m.tipo, m.descripcion, m.valor, cu.nombre AS cuenta,
                   COALESCE(cp.nombre || ' › ' || c.nombre, c.nombre) AS categoria
            FROM movimientos m JOIN cuentas cu ON cu.id = m.cuenta_id
            LEFT JOIN categorias c ON c.id = m.categoria_id LEFT JOIN categorias cp ON cp.id = c.padre_id
            WHERE m.eliminado_en IS NULL AND {where} ORDER BY {orden} LIMIT ?""",
        (*params, limite),
    ).fetchall()
    return [dict(f) for f in filas]


def datos(con: sqlite3.Connection, hasta: date | None = None) -> dict:
    h = hasta or hoy()
    inicio = catalogos.fecha_inicio(con)
    mes = Mes.de(h)
    desde_mes = max(mes.inicio, inicio)
    hay_datos = h >= inicio

    # ------------------------------------------------ cuánto dinero hay (a la fecha de corte)
    pos = resumen.posicion(con, h)
    flujos = saldos.flujos(con, None, h)
    cuentas = []
    for c in catalogos.listar_cuentas(con):
        f = flujos.get(c["id"], {})
        saldo = c["saldo_inicial"] + f.get("neto", 0)
        if c["activa"] or saldo:
            cuentas.append({"nombre": c["nombre"], "tipo": c["tipo"], "saldo": saldo,
                            "ahorro": f.get("ahorro", 0), "ultima": f.get("ultima")})
    total_cuentas = sum(c["saldo"] for c in cuentas)
    positivos = sum(c["saldo"] for c in cuentas if c["saldo"] > 0)
    for c in cuentas:  # participación sobre lo que hay en cuentas con saldo positivo
        c["participacion"] = c["saldo"] / positivos if c["saldo"] > 0 else 0

    # ------------------------------------------------ mes de la fecha de corte y comparación
    mes_actual = _bloque(resumen._totales(con, desde_mes, h)) if hay_datos else _bloque(
        {"ingreso": 0, "gasto": 0, "ahorro": 0})
    dias_mes = (h - desde_mes).days + 1 if hay_datos else 0
    anterior = None
    previo = mes.anterior()
    if previo.fin >= inicio:
        p_desde = max(previo.inicio, inicio)
        p_hasta = dia_equivalente(previo, h.day)
        if p_hasta >= p_desde:
            t = resumen._totales(con, p_desde, p_hasta)
            if any(t.values()):
                anterior = {**_bloque(t), "desde": p_desde, "hasta": p_hasta, "mes": previo}

    # ------------------------------------------------ mes a mes desde el inicio
    meses = []
    m = Mes.de(inicio)
    while m <= mes:
        t = resumen._totales(con, max(m.inicio, inicio), min(m.fin, h))
        meses.append({"mes": m, "etiqueta": m.etiqueta_corta.capitalize(), "parcial": m == mes and h < m.fin,
                      **_bloque(t)})
        m = m.siguiente()
    acumulado = _bloque(resumen._totales(con, inicio, h)) if hay_datos else mes_actual

    # ------------------------------------------------ lo que viene (registrado con fecha posterior)
    manana = h + timedelta(days=1)
    programados = _movimientos(con, "m.fecha >= ?", (manana.isoformat(),), "m.fecha, m.id", 40)
    t_prog = resumen._totales(con, manana, date.max)
    n_prog = con.execute("SELECT COUNT(*) FROM movimientos WHERE eliminado_en IS NULL AND fecha >= ?",
                         (manana.isoformat(),)).fetchone()[0]
    transf_prog = con.execute(
        "SELECT t.fecha, t.valor, o.nombre AS origen, d.nombre AS destino, t.nota FROM transferencias t "
        "JOIN cuentas o ON o.id = t.cuenta_origen_id JOIN cuentas d ON d.id = t.cuenta_destino_id "
        "WHERE t.eliminado_en IS NULL AND t.fecha >= ? ORDER BY t.fecha, t.id", (manana.isoformat(),),
    ).fetchall()

    # ------------------------------------------------ metas de ahorro (aportes hasta el corte)
    metas = []
    for f in con.execute(
        """SELECT ma.nombre, ma.monto_objetivo, ma.fecha_objetivo, COALESCE(SUM(m.valor), 0) AS acumulado
           FROM metas_ahorro ma LEFT JOIN movimientos m ON m.meta_id = ma.id AND m.tipo = 'ahorro'
             AND m.eliminado_en IS NULL AND m.fecha <= ?
           WHERE ma.activa = 1 GROUP BY ma.id ORDER BY ma.nombre""", (h.isoformat(),)
    ).fetchall():
        obj = f["monto_objetivo"]
        metas.append({**dict(f), "avance": (f["acumulado"] / obj) if obj else None})

    # ------------------------------------------------ cómo quedarías sumando lo programado
    con_programados = None
    if n_prog or transf_prog:
        pos_total = resumen.posicion(con, date.max)
        finales = {s_["id"]: s_["saldo"] for s_ in saldos.saldos(con)}
        nombres = {c["id"]: c for c in catalogos.listar_cuentas(con)}
        cuentas_prog = []
        for cid, c in nombres.items():
            hoy_ = c["saldo_inicial"] + flujos.get(cid, {}).get("neto", 0)
            final = finales.get(cid, hoy_)
            if c["activa"] or hoy_ or final:
                cuentas_prog.append({"nombre": c["nombre"], "hoy": hoy_, "cambio": final - hoy_, "final": final})
        por_mes = []
        for f in con.execute(
            "SELECT substr(fecha, 1, 7) AS mes, "
            "SUM(CASE WHEN tipo = 'ingreso' THEN valor ELSE 0 END) AS ingresos, "
            "SUM(CASE WHEN tipo = 'gasto' THEN valor ELSE 0 END) AS gastos, "
            "SUM(CASE WHEN tipo = 'ahorro' THEN valor ELSE 0 END) AS ahorro, COUNT(*) AS cantidad "
            "FROM movimientos WHERE eliminado_en IS NULL AND fecha >= ? GROUP BY 1 ORDER BY 1",
            (manana.isoformat(),),
        ).fetchall():
            m_ = Mes.de(f["mes"])
            por_mes.append({"mes": m_, "ingresos": f["ingresos"], "gastos": f["gastos"], "ahorro": f["ahorro"],
                            "neto": f["ingresos"] - f["gastos"], "cantidad": f["cantidad"]})
        # El mes del corte completo (lo ya pasado + lo programado dentro del mes).
        mes_completo = None
        if hay_datos and h < mes.fin and any(x["mes"] == mes for x in por_mes):
            mes_completo = _bloque(resumen._totales(con, desde_mes, mes.fin))
        con_programados = {
            "posicion": pos_total, "cuentas": cuentas_prog, "por_mes": por_mes, "mes_completo": mes_completo,
            "neto": t_prog["ingreso"] - t_prog["gasto"],
            "cambio_disponible": pos_total["disponible"] - pos["disponible"],
            "ultima_fecha": con.execute(
                "SELECT MAX(fecha) FROM (SELECT fecha FROM movimientos WHERE eliminado_en IS NULL AND fecha >= ? "
                "UNION ALL SELECT fecha FROM transferencias WHERE eliminado_en IS NULL AND fecha >= ?)",
                (manana.isoformat(), manana.isoformat())).fetchone()[0],
        }

    gastos_mes = _por_categoria(con, "gasto", desde_mes, h) if hay_datos else []
    ingresos_total = _por_categoria(con, "ingreso", inicio, h) if hay_datos else []
    d = {
        "corte": h,
        "generado": datetime.now(),
        "inicio": inicio,
        "mes": mes,
        "desde_mes": desde_mes,
        "dias_mes": dias_mes,
        "posicion": pos,
        "cuentas": cuentas,
        "total_cuentas": total_cuentas,
        "mes_actual": mes_actual,
        "anterior": anterior,
        "gastos_mes": gastos_mes,
        "gastos_total": _por_categoria(con, "gasto", inicio, h) if hay_datos else [],
        "ingresos_mes": _por_categoria(con, "ingreso", desde_mes, h) if hay_datos else [],
        "ingresos_total": ingresos_total,
        "naturaleza": _por_campo(con, "naturaleza", desde_mes, h) if hay_datos else [],
        "necesidad": _por_campo(con, "necesidad", desde_mes, h) if hay_datos else [],
        "mayores_gastos": _movimientos(con, "m.tipo = 'gasto' AND m.fecha BETWEEN ? AND ?",
                                       (desde_mes.isoformat(), h.isoformat()), "m.valor DESC, m.fecha", 10),
        "meses": meses,
        "acumulado": acumulado,
        "programados": programados,
        "programados_total": t_prog,
        "programados_cantidad": n_prog,
        "transferencias_programadas": [dict(t) for t in transf_prog],
        "disponible_con_programados": resumen.posicion(con, date.max)["disponible"],
        "con_programados": con_programados,
        "me_deben": [p for p in prestamos.listar(con, incluir_saldados=False)],
        "metas": metas,
        "punto_partida_mes": next((c["total"] for c in _por_categoria(con, "ingreso", desde_mes, h)
                                   if c["nombre"] == CATEGORIA_PUNTO_PARTIDA), 0) if hay_datos else 0,
    }
    d["observaciones"] = observaciones(d)
    return d


def observaciones(d: dict) -> list[dict]:
    """Hechos que se leen de las cifras. Nivel: 'bueno', 'alerta' o 'info'. Sin proyecciones."""
    obs: list[dict] = []
    act, mes = d["mes_actual"], d["mes"]
    nombre = f"lo que va de {mes.nombre}" if d["corte"] < mes.fin else mes.nombre

    pp = d["punto_partida_mes"]
    if pp:
        obs.append({"nivel": "info", "texto":
                    f"De los ingresos de {nombre}, {cop(pp)} son tu punto de partida (el dinero con el que "
                    f"empezaste a usar el sistema), no un ingreso nuevo. Sin él, tus ingresos del periodo "
                    f"son {cop(act['ingresos'] - pp)}."})

    ingresos_reales = act["ingresos"] - pp
    if act["gastos"] and ingresos_reales > 0:
        uso = act["gastos"] / ingresos_reales
        en_curso = d["corte"] < mes.fin
        # Con el mes en curso no se alarma: el salario puede llegar después que los gastos fijos.
        nivel = "info" if en_curso and uso > 0.8 else ("alerta" if uso > 1 else ("info" if uso > 0.8 else "bueno"))
        obs.append({"nivel": nivel, "texto":
                    f"En {nombre} tus gastos ({cop(act['gastos'])}) equivalen al {porcentaje(uso)} de tus "
                    f"ingresos{' sin contar el punto de partida' if pp else ''} ({cop(ingresos_reales)})."
                    + (" El mes aún no termina: revisa si faltan ingresos por llegar." if en_curso and uso > 1 else "")})
    elif act["gastos"] and ingresos_reales <= 0:
        obs.append({"nivel": "info", "texto":
                    f"En {nombre} llevas {cop(act['gastos'])} en gastos y aún no registras ingresos nuevos."})

    if act["gastos"] and d["dias_mes"]:
        obs.append({"nivel": "info", "texto":
                    f"Promedio de gasto en {nombre}: {cop(round(act['gastos'] / d['dias_mes']))} por día "
                    f"({d['dias_mes']} {'día' if d['dias_mes'] == 1 else 'días'})."})

    if d["gastos_mes"]:
        top = d["gastos_mes"][0]
        obs.append({"nivel": "info", "texto":
                    f"{top['nombre']} es tu mayor gasto en {nombre}: {cop(top['total'])}, "
                    f"el {porcentaje(top['participacion'])} del total."})

    disc = next((x for x in d["necesidad"] if x["clave"] == "discrecional"), None)
    if disc and disc["participacion"] >= 0.25:
        obs.append({"nivel": "info", "texto":
                    f"Los gastos discrecionales (no esenciales) suman {cop(disc['total'])}, el "
                    f"{porcentaje(disc['participacion'])} de tus gastos: es el margen más directo si necesitas recortar."})

    ant = d["anterior"]
    if ant and ant["gastos"] and act["gastos"]:
        v = (act["gastos"] - ant["gastos"]) / ant["gastos"]
        if abs(v) >= 0.05:
            obs.append({"nivel": "alerta" if v > 0 else "bueno", "texto":
                        f"Llevas {porcentaje(abs(v))} {'más' if v > 0 else 'menos'} en gastos que en el mismo "
                        f"periodo de {ant['mes'].nombre} ({cop(act['gastos'])} frente a {cop(ant['gastos'])})."})

    if act["tasa_ahorro"] is not None and act["ahorro"] and ingresos_reales > 0:
        obs.append({"nivel": "bueno", "texto":
                    f"Apartaste {cop(act['ahorro'])} como ahorro en {nombre}, el "
                    f"{porcentaje(act['ahorro'] / ingresos_reales)} de tus ingresos"
                    f"{' sin contar el punto de partida' if pp else ''}."})

    for c in d["cuentas"]:
        if c["saldo"] < 0:
            obs.append({"nivel": "alerta", "texto":
                        f"{c['nombre']} aparece con saldo negativo ({cop(c['saldo'])}): probablemente falta "
                        f"registrar un ingreso o dinero que pasaste a esa cuenta desde otra."})

    if d["programados_cantidad"]:
        t = d["programados_total"]
        neto = t["ingreso"] - t["gasto"]
        obs.append({"nivel": "info", "texto":
                    f"Tienes {d['programados_cantidad']} movimiento(s) registrados con fecha posterior al "
                    f"{fecha_corta(d['corte'])} ({cop(neto, True)} netos). No están sumados en las cifras a hoy; "
                    f"con ellos tu disponible quedaría en {cop(d['disponible_con_programados'])} "
                    f"(detalle en «Cómo quedarías con lo programado»)."})

    if d["posicion"]["me_deben"]:
        obs.append({"nivel": "info", "texto":
                    f"Te deben {cop(d['posicion']['me_deben'])}. No está en tus cuentas ni en el disponible "
                    f"hasta que te paguen."})
    return obs


def nombre_archivo(corte: date) -> str:
    return f"reporte-finanzas-{corte.isoformat()}.pdf"


def titulo_fecha(f: date) -> str:
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"
