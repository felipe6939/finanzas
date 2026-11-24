"""Reporte PDF: cifras con corte a hoy, programados aparte, HTML y PDF generados localmente."""
import pytest

from finanzas.analytics import reporte
from finanzas.services import catalogos, movimientos, punto_partida, saldos
from finanzas.web import reporte_pdf
from tests.conftest import id_categoria, id_cuenta
from tests.test_web import web  # noqa: F401  (fixture)


def _mov(con, cuenta, tipo, valor, fecha, cat="Mercado", desc="x"):
    movimientos.crear(con, movimientos.DatosMovimiento(
        tipo=tipo, fecha=fecha, valor=valor, descripcion=desc, cuenta_id=id_cuenta(con, cuenta),
        categoria_id=id_categoria(con, cat, tipo) if tipo != "ahorro" else None))


@pytest.fixture
def con_datos(con):  # hoy = 2026-11-20 (conftest)
    catalogos.actualizar_inicio(con, "2026-10-01", 0)
    _mov(con, "Nequi", "ingreso", 1_000_000, "2026-10-02", cat="Salario")
    _mov(con, "Nequi", "gasto", 200_000, "2026-10-10")
    _mov(con, "Nequi", "ingreso", 1_000_000, "2026-11-02", cat="Salario")
    _mov(con, "Nequi", "gasto", 300_000, "2026-11-05", desc="Mercado grande")
    _mov(con, "Efectivo", "gasto", 20_000, "2026-11-19", cat="Café y snacks")
    _mov(con, "Nequi", "ahorro", 100_000, "2026-11-15")
    _mov(con, "Nequi", "gasto", 500_000, "2026-11-28", desc="Programado")      # después del corte
    _mov(con, "Nequi", "ingreso", 2_000_000, "2026-12-01", cat="Salario")       # después del corte
    saldos.transferir(con, "2026-11-10", 50_000, id_cuenta(con, "Nequi"), id_cuenta(con, "Efectivo"))
    return con


def test_cifras_con_corte_a_hoy(con_datos):
    d = reporte.datos(con_datos)
    assert d["corte"].isoformat() == "2026-11-20"
    a = d["mes_actual"]
    assert (a["ingresos"], a["gastos"], a["ahorro"]) == (1_000_000, 320_000, 100_000)
    assert d["total_cuentas"] == 2_000_000 - 520_000                     # sin los programados
    cuentas = {c["nombre"]: c["saldo"] for c in d["cuentas"]}
    assert cuentas["Efectivo"] == 30_000 and cuentas["Nequi"] == 1_450_000
    assert d["posicion"]["disponible"] == 1_480_000 - 100_000
    assert d["disponible_con_programados"] == 1_380_000 - 500_000 + 2_000_000
    assert d["programados_cantidad"] == 2
    assert [m["etiqueta"] for m in d["meses"]] == ["Oct 26", "Nov 26"] and d["meses"][1]["parcial"]
    assert d["acumulado"]["gastos"] == 520_000
    assert d["anterior"]["gastos"] == 200_000                             # 1 al 20 de octubre
    assert d["mayores_gastos"][0]["descripcion"] == "Mercado grande"
    assert d["gastos_mes"][0]["nombre"] == "Alimentación"
    textos = " ".join(o["texto"] for o in d["observaciones"])
    assert "32 %" in textos                                                # gastos / ingresos
    assert "2 movimiento(s)" in textos and "60 % más en gastos" in textos


def test_punto_de_partida_se_aclara(con):
    catalogos.actualizar_inicio(con, "2026-11-01", 0)
    cu = catalogos.obtener_cuenta(con, id_cuenta(con, "Nequi"))
    catalogos.guardar_cuenta(con, cu["id"], cu["nombre"], cu["tipo"], cu["alias"], 800_000, True)
    punto_partida.registrar_como_ingresos(con)
    _mov(con, "Nequi", "gasto", 100_000, "2026-11-05")
    d = reporte.datos(con)
    assert d["punto_partida_mes"] == 800_000
    assert any("no un ingreso nuevo" in o["texto"] for o in d["observaciones"])


def test_graficos_svg(con_datos):
    g = reporte_pdf.graficos(reporte.datos(con_datos))
    assert g["mensual"].startswith("<svg") and "Ingresos" in g["mensual"]
    assert "Alimentación" in g["gastos_mes"]
    muchas = [{"nombre": f"C{i}", "total": 10 - i, "participacion": 0.1} for i in range(10)]
    assert "Otras (3)" in reporte_pdf.svg_categorias(muchas, "#000")


def test_reporte_sin_datos(con):
    d = reporte.datos(con)
    assert d["mes_actual"]["gastos"] == 0 and d["observaciones"] == []


def test_web_ver_reporte(web):
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-17"})
    assert "Descargar reporte PDF" in web.get("/").text
    r = web.get("/reporte")
    assert r.status_code == 200 and "Reporte de finanzas personales" in r.text
    assert "¿A dónde va tu dinero?" in r.text and "$&nbsp;45.000" in r.text and "<svg" in r.text


def test_web_sin_navegador(web, monkeypatch):
    monkeypatch.setenv("FINANZAS_NAVEGADOR", "C:/no/existe.exe")
    r = web.get("/reporte.pdf")
    assert "No encontré Microsoft Edge ni Google Chrome" in r.text


@pytest.mark.skipif(reporte_pdf.navegador() is None, reason="Sin Edge/Chrome en este equipo")
def test_web_descarga_pdf(web):
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-17"})
    r = web.get("/reporte.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF" and len(r.content) > 5_000
    assert 'filename="reporte-finanzas-2026-11-20.pdf"' in r.headers["content-disposition"]


def test_seccion_con_programados(con_datos):
    d = reporte.datos(con_datos)
    cp = d["con_programados"]
    assert cp["neto"] == 2_000_000 - 500_000
    assert cp["posicion"]["disponible"] == d["disponible_con_programados"] == 2_880_000
    assert cp["cambio_disponible"] == 2_880_000 - d["posicion"]["disponible"]
    nequi = next(c for c in cp["cuentas"] if c["nombre"] == "Nequi")
    assert (nequi["hoy"], nequi["cambio"], nequi["final"]) == (1_450_000, 1_500_000, 2_950_000)
    assert [(m["mes"].clave, m["neto"]) for m in cp["por_mes"]] == [("2026-11", -500_000), ("2026-12", 2_000_000)]
    assert cp["mes_completo"]["gastos"] == 320_000 + 500_000
    assert cp["ultima_fecha"] == "2026-12-01"


def test_sin_programados_no_hay_seccion(con):
    _mov(con, "Nequi", "gasto", 10_000, "2026-11-18")
    assert reporte.datos(con)["con_programados"] is None


def test_web_numeracion_secciones(web):
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-18"})
    web.post("/movimientos/rapido", data={"texto": "30.000 almuerzo", "fecha": "2026-11-25"})
    r = web.get("/reporte").text
    assert "6. Lo que viene" in r and "7. Cómo quedarías con lo programado" in r
    assert "sí se suman" in r
