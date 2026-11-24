"""Punto de partida como ingresos del primer mes y rango de meses del tablero."""
from datetime import date

import pytest

from finanzas.analytics import resumen
from finanzas.core.fechas import Mes
from finanzas.services import catalogos, punto_partida
from finanzas.services.errores import ErrorValidacion
from tests.conftest import id_cuenta
from tests.test_web import web  # noqa: F401  (fixture)


def _saldos(con):
    for nombre, saldo in (("Nequi", 1_336_000), ("Efectivo", 697_000)):
        c = catalogos.obtener_cuenta(con, id_cuenta(con, nombre))
        catalogos.guardar_cuenta(con, c["id"], c["nombre"], c["tipo"], c["alias"], saldo)


def test_saldos_pasan_a_ingresos_sin_cambiar_disponible(con):
    _saldos(con)
    antes = resumen.posicion(con, date(2026, 11, 20))
    hechos = punto_partida.registrar_como_ingresos(con)
    assert sorted(h["valor"] for h in hechos) == [697_000, 1_336_000]
    assert resumen.posicion(con, date(2026, 11, 20)) == antes
    assert resumen.kpis(con, Mes(2026, 11))["actual"]["ingresos"] == 2_033_000
    assert catalogos.saldo_inicial_total(con) == 0
    with pytest.raises(ErrorValidacion):  # solo una vez
        punto_partida.registrar_como_ingresos(con)


def test_evolucion_de_noviembre_a_diciembre_del_otro_anio(con):
    filas = resumen.evolucion(con, Mes(2026, 11))
    assert filas[0]["mes"] == "2026-11" and filas[-1]["mes"] == "2027-12" and len(filas) == 14
    assert filas[0]["futuro"] is False and all(f["futuro"] and f["ingresos"] is None for f in filas[1:])


def test_no_hay_meses_antes_del_inicio(web):
    r = web.get("/?mes=2026-09")
    assert "Noviembre 2026" in r.text and 'aria-label="Mes anterior"' not in r.text


def test_ritmo_sin_mes_anterior_al_inicio(con):
    assert resumen.ritmo_de_gasto(con, Mes(2026, 11))["anterior"] == []


def test_formulario_simplificado_no_toca_saldos(web, con):
    _saldos(con)
    punto_partida.registrar_como_ingresos(con)
    r = web.get("/configuracion")
    assert "Punto de partida y deudas a tu favor" in r.text and "$ 2.033.000" in r.text
    assert 'name="saldo_' not in r.text and 'name="fecha_inicio"' not in r.text
    # aunque alguien envíe saldos o fecha a mano, se ignoran
    r = web.post("/configuracion/inicio", data={"ahorro_inicial": "300.000", "saldo_1": "999.999",
                                                 "fecha_inicio": "2026-11-18"})
    assert "Ahorro inicial guardado" in r.text
    assert catalogos.saldo_inicial_total(con) == 0 and catalogos.ahorro_inicial(con) == 300_000
    assert catalogos.fecha_inicio(con).isoformat() == "2026-11-16"
