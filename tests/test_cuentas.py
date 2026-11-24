"""Panel de cuentas: saldo por cuenta y dinero entre cuentas propias."""
from datetime import date

import pytest

from finanzas import demo
from finanzas.analytics import resumen
from finanzas.services import movimientos, prestamos, saldos
from finanzas.services.errores import ErrorValidacion
from tests.conftest import id_categoria, id_cuenta
from tests.test_web import web  # noqa: F401  (fixture)


def _mov(con, cuenta, tipo, valor, fecha="2026-11-17", cat="Mercado"):
    movimientos.crear(con, movimientos.DatosMovimiento(
        tipo=tipo, fecha=fecha, valor=valor, descripcion="x", cuenta_id=id_cuenta(con, cuenta),
        categoria_id=id_categoria(con, cat, tipo) if tipo != "ahorro" else None))


def _saldo(con, cuenta):
    return next(s["saldo"] for s in saldos.saldos(con) if s["nombre"] == cuenta)


def test_saldos_por_cuenta_suman_el_patrimonio(con):
    _mov(con, "Nequi", "ingreso", 1_000_000, cat="Salario")
    _mov(con, "Nequi", "gasto", 50_000)
    _mov(con, "Nequi", "ahorro", 200_000)                      # apartado: no sale de la cuenta
    _mov(con, "Efectivo", "gasto", 30_000, fecha="2026-11-25")  # programado
    saldos.transferir(con, "2026-11-18", 200_000, id_cuenta(con, "Nequi"), id_cuenta(con, "Efectivo"), "cajero")
    pid = prestamos.guardar_inicial(con, None, "Ana", 100_000)
    prestamos.registrar_abono(con, pid, "2026-11-19", 40_000, id_cuenta(con, "BBVA"))
    assert _saldo(con, "Nequi") == 1_000_000 - 50_000 - 200_000
    assert _saldo(con, "Efectivo") == 200_000 - 30_000
    assert _saldo(con, "BBVA") == 40_000
    assert sum(s["saldo"] for s in saldos.saldos(con)) == resumen.posicion(con, date.max)["patrimonio"]
    efectivo = next(s for s in saldos.saldos(con) if s["nombre"] == "Efectivo")
    assert efectivo["programado"] == -30_000


def test_transferencia_no_cambia_cifras_del_mes(con):
    _mov(con, "Nequi", "ingreso", 500_000, cat="Salario")
    from finanzas.core.fechas import Mes
    antes = resumen.kpis(con, Mes(2026, 11))
    saldos.transferir(con, "2026-11-18", 100_000, id_cuenta(con, "Nequi"), id_cuenta(con, "Efectivo"))
    despues = resumen.kpis(con, Mes(2026, 11))
    assert antes["actual"] == despues["actual"] and antes["posicion"] == despues["posicion"]


@pytest.mark.parametrize("cambios,error", [
    ({"destino": "Nequi"}, "distintas"),
    ({"valor": 0}, "mayor que cero"),
    ({"fecha": "2026-11-10"}, "anterior al inicio"),
])
def test_validaciones_transferencia(con, cambios, error):
    d = {"fecha": "2026-11-18", "valor": 10_000, "origen": "Nequi", "destino": "Efectivo"} | cambios
    with pytest.raises(ErrorValidacion) as e:
        saldos.transferir(con, d["fecha"], d["valor"], id_cuenta(con, d["origen"]), id_cuenta(con, d["destino"]))
    assert error in " ".join(e.value.errores)


def test_eliminar_y_reiniciar(con):
    tid = saldos.transferir(con, "2026-11-18", 10_000, id_cuenta(con, "Nequi"), id_cuenta(con, "Efectivo"))
    saldos.eliminar_transferencia(con, tid)
    assert _saldo(con, "Efectivo") == 0 and saldos.listar_transferencias(con) == []
    saldos.transferir(con, "2026-11-18", 10_000, id_cuenta(con, "Nequi"), id_cuenta(con, "Efectivo"))
    demo.reiniciar(con)
    assert con.execute("SELECT COUNT(*) FROM transferencias").fetchone()[0] == 0


def test_web_panel_cuentas(web):
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-17"})
    r = web.post("/cuentas/transferir", data={"origen": "1", "destino": "4", "valor": "100k", "fecha": "2026-11-18"})
    assert "Movimiento entre cuentas registrado" in r.text
    assert "Nequi → Efectivo" in r.text and "$ 100.000" in r.text
    assert "Saldo negativo" in r.text  # Nequi quedó en −145.000: el panel lo advierte


def test_saldos_del_mes(con):
    from finanzas.core.fechas import Mes
    from finanzas.services import catalogos
    catalogos.actualizar_inicio(con, "2026-10-01", 0)
    nequi, efectivo = id_cuenta(con, "Nequi"), id_cuenta(con, "Efectivo")
    _mov(con, "Nequi", "ingreso", 500_000, fecha="2026-10-05", cat="Salario")      # mes anterior
    _mov(con, "Nequi", "gasto", 100_000, fecha="2026-10-20")
    saldos.transferir(con, "2026-10-21", 50_000, nequi, efectivo)
    _mov(con, "Nequi", "ingreso", 300_000, fecha="2026-11-03", cat="Salario")
    _mov(con, "Nequi", "gasto", 40_000, fecha="2026-11-10")
    _mov(con, "Nequi", "gasto", 25_000, fecha="2026-11-28")                         # programado del mes
    _mov(con, "Nequi", "gasto", 99_000, fecha="2026-12-02")                         # otro mes: no cuenta
    _mov(con, "Nequi", "ahorro", 60_000, fecha="2026-11-12")
    saldos.transferir(con, "2026-11-15", 20_000, nequi, efectivo)
    mes = {s["nombre"]: s for s in saldos.saldos_mes(con, Mes(2026, 11))}
    n, e = mes["Nequi"], mes["Efectivo"]
    assert n["inicio"] == 350_000 and e["inicio"] == 50_000
    assert (n["ingresos"], n["gastos"], n["enviado"]) == (300_000, 65_000, 20_000)
    assert n["final"] == 350_000 + 300_000 - 65_000 - 20_000
    assert n["programado"] == -25_000 and n["ahorro"] == 60_000
    assert e["recibido"] == 20_000 and e["final"] == 70_000
    # El final de noviembre es el inicio de diciembre; el total suma todos los meses.
    dic = {s["nombre"]: s for s in saldos.saldos_mes(con, Mes(2026, 12))}
    assert dic["Nequi"]["inicio"] == n["final"]
    assert _saldo(con, "Nequi") == n["final"] - 99_000


def test_web_submodulos_cuentas(web):
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-17"})
    r = web.get("/cuentas")
    assert "Cuentas Noviembre 2026" in r.text and "Cuentas Total" in r.text
    assert "Al empezar noviembre" in r.text and "Salió en el mes" in r.text
    r = web.get("/cuentas?vista=total")
    assert "Total en cuentas" in r.text and "Al empezar noviembre" not in r.text
    r = web.post("/cuentas/transferir", data={"origen": "1", "destino": "4", "valor": "10k",
                                              "fecha": "2026-11-18", "vista": "total"})
    assert "Movimiento entre cuentas registrado" in r.text and "Total en cuentas" in r.text
