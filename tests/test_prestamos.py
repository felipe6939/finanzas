"""«Me deben»: deudas iniciales a favor y pagos recibidos."""
from datetime import date

import pytest

from finanzas import demo
from finanzas.analytics import resumen
from finanzas.services import catalogos, prestamos
from finanzas.services.errores import ErrorValidacion
from tests.conftest import id_cuenta
from tests.test_web import web  # noqa: F401  (fixture)

HOY = date(2026, 11, 20)


def test_deuda_inicial_no_cambia_disponible(con):
    antes = resumen.posicion(con, HOY)
    prestamos.guardar_inicial(con, None, "Andrés", 200_000, "préstamo de octubre")
    despues = resumen.posicion(con, HOY)
    assert despues["disponible"] == antes["disponible"]
    assert despues["me_deben"] == 200_000


def test_pago_recibido_vuelve_al_disponible_sin_ser_ingreso(con):
    pid = prestamos.guardar_inicial(con, None, "Andrés", 200_000)
    base = resumen.posicion(con, HOY)["disponible"]
    prestamos.registrar_abono(con, pid, "2026-11-18", 50_000, id_cuenta(con, "Nequi"))
    pos = resumen.posicion(con, HOY)
    assert pos["disponible"] == base + 50_000
    assert pos["me_deben"] == 150_000
    assert resumen.posicion(con, date(2026, 11, 17))["me_deben"] == 200_000  # antes del pago
    from finanzas.core.fechas import Mes
    assert resumen.kpis(con, Mes(2026, 11))["actual"]["ingresos"] == 0  # no es ingreso


@pytest.mark.parametrize("fecha,valor,error", [
    ("2026-11-18", 250_000, "supera lo pendiente"),
    ("2026-11-25", 10_000, "futura"),
    ("2026-11-10", 10_000, "anterior a la fecha de inicio"),
    ("2026-11-18", 0, "mayor que cero"),
])
def test_validaciones_del_pago(con, fecha, valor, error):
    pid = prestamos.guardar_inicial(con, None, "Andrés", 200_000)
    with pytest.raises(ErrorValidacion) as e:
        prestamos.registrar_abono(con, pid, fecha, valor, id_cuenta(con, "Nequi"))
    assert error in " ".join(e.value.errores)


def test_no_baja_de_lo_pagado_ni_se_elimina_con_pagos(con):
    pid = prestamos.guardar_inicial(con, None, "Andrés", 200_000)
    aid = prestamos.registrar_abono(con, pid, "2026-11-18", 80_000, id_cuenta(con, "Nequi"))
    with pytest.raises(ErrorValidacion):
        prestamos.guardar_inicial(con, pid, "Andrés", 50_000)
    with pytest.raises(ErrorValidacion):
        prestamos.eliminar(con, pid)
    prestamos.eliminar_abono(con, aid)
    prestamos.eliminar(con, pid)
    assert prestamos.listar(con) == []


def test_sin_borrado_fisico_y_con_auditoria(con):
    import sqlite3
    pid = prestamos.guardar_inicial(con, None, "Andrés", 200_000)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM prestamos WHERE id = ?", (pid,))
    acciones = [f["accion"] for f in con.execute(
        "SELECT accion FROM auditoria WHERE tabla = 'prestamos' ORDER BY id")]
    assert acciones == ["crear"]


def test_fecha_inicio_no_puede_saltar_un_pago(con):
    pid = prestamos.guardar_inicial(con, None, "Andrés", 200_000)
    prestamos.registrar_abono(con, pid, "2026-11-17", 10_000, id_cuenta(con, "Nequi"))
    with pytest.raises(ErrorValidacion):
        catalogos.actualizar_inicio(con, "2026-11-18", 0)


def test_reiniciar_borra_pagos_y_conserva_deudas(con):
    pid = prestamos.guardar_inicial(con, None, "Andrés", 200_000)
    prestamos.registrar_abono(con, pid, "2026-11-18", 50_000, id_cuenta(con, "Nequi"))
    demo.reiniciar(con)
    p = prestamos.obtener(con, pid)
    assert p["pendiente"] == 200_000 and p["abonos"] == []


def test_web_me_deben(web):
    r = web.post("/configuracion/deuda", data={"persona": "Andrés", "valor": "200k", "nota": ""})
    assert "Deuda a tu favor guardada" in r.text and "$ 200.000" in r.text
    r = web.post("/configuracion/abono", data={"prestamo_id": "1", "fecha": "2026-11-18",
                                               "valor": "50.000", "cuenta_id": "1"})
    assert "Pago recibido registrado" in r.text and "$ 150.000" in r.text
    r = web.post("/configuracion/abono", data={"prestamo_id": "1", "fecha": "2026-11-18",
                                               "valor": "1.000.000", "cuenta_id": "1"})
    assert "supera lo pendiente" in r.text
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo"})  # el tablero vacío no muestra KPIs
    assert "Te deben: $ 150.000" in web.get("/").text
