from datetime import date

import pytest

from finanzas.importers import importacion
from finanzas.importers.interprete import interpretar, interpretar_linea_fecha
from tests.conftest import id_categoria, id_cuenta


@pytest.fixture
def ctx(con):
    return importacion.construir_contexto(con)


def mov(lineas, ctx):
    return [i for i in interpretar(lineas, ctx) if i.clase in ("movimiento", "sin_valor")]


@pytest.mark.parametrize("texto,esperado", [
    ("16/11", date(2026, 11, 16)),
    ("16-11", date(2026, 11, 16)),
    ("16/11/2026", date(2026, 11, 16)),
    ("Lunes 16/11", date(2026, 11, 16)),
    ("16 nov", date(2026, 11, 16)),
    ("16 de noviembre", date(2026, 11, 16)),
    ("sábado 14 de noviembre de 2026:", date(2026, 11, 14)),
])
def test_lineas_de_fecha(texto, esperado):
    assert interpretar_linea_fecha(texto, date(2026, 11, 20))[0] == esperado


@pytest.mark.parametrize("texto,esperada", [
    ("20/11", date(2026, 11, 20)),   # en 3 días: este año, no el pasado
    ("28/12", date(2026, 12, 28)),   # programado para el próximo mes
    ("2/01", date(2027, 1, 2)),      # enero que viene
    ("5/06", date(2026, 6, 5)),      # más cerca hacia atrás
])
def test_anio_deducido_es_la_fecha_mas_cercana(texto, esperada):
    fecha, nota = interpretar_linea_fecha(texto, date(2026, 11, 17))
    assert fecha == esperada
    assert (nota is None) == (esperada.year == 2026)


@pytest.mark.parametrize("texto", ["45.000 almuerzo", "15 mil almuerzo", "noviembre 2026"])
def test_no_son_fechas(texto):
    assert interpretar_linea_fecha(texto, date(2026, 11, 20)) is None


@pytest.mark.parametrize("texto,valor", [
    ("45.000 almuerzo", 45_000),
    ("almuerzo 45000", 45_000),
    ("45k almuerzo", 45_000),
    ("45 mil almuerzo", 45_000),
    ("20 lucas almuerzo", 20_000),
    ("$45.000 almuerzo", 45_000),
    ("1.200.000 arriendo", 1_200_000),
    ("1,2 millones arriendo", 1_200_000),
    ("2 palos arriendo", 2_000_000),
    ("12.5k bus", 12_500),
])
def test_valores(ctx, texto, valor):
    (m,) = mov(["16/11", texto], ctx)
    assert m.valor == valor


def test_formato_completo(con, ctx):
    lineas = [
        "16/11",
        "45.000 almuerzo",
        "gasolina 80.000 bancolombia",
        "+3.200.000 salario bbva",
        "ahorro 300.000",
        "25.500 farmacia #salud (para mamá)",
    ]
    a, b, c, d, e = mov(lineas, ctx)
    assert (a.tipo, a.valor, a.descripcion, a.cuenta_id) == ("gasto", 45_000, "Almuerzo", id_cuenta(con, "Nequi"))
    assert a.origen["cuenta"] == "defecto"
    assert a.categoria_id == id_categoria(con, "Restaurantes y almuerzos")
    assert b.cuenta_id == id_cuenta(con, "Bancolombia") and b.descripcion == "Gasolina"
    assert b.categoria_id == id_categoria(con, "Gasolina")
    assert c.tipo == "ingreso" and c.valor == 3_200_000 and c.cuenta_id == id_cuenta(con, "BBVA")
    assert c.categoria_id == id_categoria(con, "Salario", "ingreso")
    assert d.tipo == "ahorro" and d.valor == 300_000 and d.descripcion == "Ahorro"
    assert e.categoria_id == id_categoria(con, "Salud") and e.origen["categoria"] == "etiqueta"
    assert e.observaciones == "para mamá" and e.descripcion == "Farmacia"
    assert all(m.fecha == date(2026, 11, 16) for m in (a, b, c, d, e))


def test_no_inventa_datos(ctx):
    sin_fecha, = mov(["45.000 almuerzo"], ctx)
    assert sin_fecha.fecha is None

    varios, = mov(["16/11", "30.000 y 45.000 almuerzo"], ctx)
    assert varios.valor is None and "varios valores" in varios.notas[0]

    sin_categoria, = mov(["16/11", "45.000 cosa rara xyz"], ctx)
    assert sin_categoria.categoria_id is None

    etiqueta_mala, = mov(["16/11", "45.000 almuerzo #inexistente"], ctx)
    assert etiqueta_mala.categoria_id is None  # no se adivina con reglas si la etiqueta falla


def test_numero_pequeno_es_debil_y_avisa(ctx):
    m, = mov(["16/11", "500 bus"], ctx)
    assert m.valor == 500 and "bajo" in m.notas[0]


def test_numeros_que_no_son_valor_quedan_en_la_descripcion(ctx):
    m, = mov(["16/11", "2 almuerzos 30.000"], ctx)
    assert m.valor == 30_000 and m.descripcion == "2 almuerzos"


def test_varias_cuentas_es_ambiguo(ctx):
    m, = mov(["16/11", "50.000 bancolombia a nequi"], ctx)
    assert m.cuenta_id is None and "varias cuentas" in m.notas[0]


def test_conector_de_cuenta_se_elimina(ctx, con):
    m, = mov(["16/11", "almuerzo 15.000 en efectivo"], ctx)
    assert m.descripcion == "Almuerzo" and m.cuenta_id == id_cuenta(con, "Efectivo")


def test_fecha_en_la_linea_aplica_solo_a_esa_linea(ctx):
    a, b = mov(["16/11", "17/11 45.000 almuerzo", "12k bus"], ctx)
    assert a.fecha == date(2026, 11, 17) and b.fecha == date(2026, 11, 16)


def test_encabezados_sin_valor(ctx):
    a, = mov(["Movimientos noviembre 2026"], ctx)
    assert a.clase == "sin_valor"


def test_regla_mas_especifica_gana(ctx, con):
    m, = mov(["16/11", "80.000 gas natural"], ctx)
    assert m.categoria_id == id_categoria(con, "Servicios públicos")
