"""Movimientos con fecha futura: cuentan desde que se registran y se identifican como programados."""
from datetime import date

from finanzas.analytics import resumen
from finanzas.core.fechas import Mes
from finanzas.services import movimientos
from tests.conftest import id_cuenta, id_categoria
from tests.test_web import web  # noqa: F401  (fixture)

# conftest: hoy = 20 nov 2026, inicio = 16 nov 2026


def _mov(con, fecha, valor, tipo="gasto", cat="Arriendo"):
    return movimientos.crear(con, movimientos.DatosMovimiento(
        tipo=tipo, fecha=fecha, valor=valor, descripcion=cat, cuenta_id=id_cuenta(con, "Nequi"),
        categoria_id=id_categoria(con, cat, tipo)))


def test_programado_mueve_las_cuentas_desde_ya(con):
    _mov(con, "2026-11-18", 20_000, cat="Mercado")
    _mov(con, "2026-11-28", 550_000)                       # arriendo programado este mes
    _mov(con, "2026-12-01", 3_000_000, "ingreso", "Salario")  # salario del mes que viene
    k = resumen.kpis(con, Mes(2026, 11))
    assert k["posicion"]["disponible"] == -20_000 - 550_000 + 3_000_000
    assert k["actual"]["gastos"] == 570_000                 # noviembre incluye el arriendo programado
    assert k["periodo"].hasta == date(2026, 11, 28)          # y se compara hasta el día 28
    assert k["hay_programado"] and k["programado"]["ingreso"] == 3_000_000
    assert resumen.kpis(con, Mes(2026, 12))["actual"]["ingresos"] == 3_000_000


def test_evolucion_muestra_meses_futuros_con_programados(con):
    _mov(con, "2026-12-01", 3_000_000, "ingreso", "Salario")
    filas = {f["mes"]: f for f in resumen.evolucion(con, Mes(2026, 11))}
    assert filas["2026-12"]["ingresos"] == 3_000_000 and filas["2026-12"]["futuro"]
    assert filas["2027-01"]["ingresos"] is None


def test_web_registro_rapido_con_fecha_futura(web):
    r = web.post("/movimientos/rapido", data={"texto": "550.000 arriendo", "fecha": "2026-11-28"})
    assert "Registrado" in r.text
    assert "programado" in web.get("/movimientos").text
    assert "Incluye programados" in web.get("/").text
