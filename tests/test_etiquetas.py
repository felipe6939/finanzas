"""Autocompletado de #categoría: cada etiqueta sugerida debe resolver exactamente su categoría."""
import json

import pytest

from finanzas.importers import importacion
from finanzas.importers.interprete import clave_etiqueta, etiquetas_categorias, interpretar
from finanzas.services import catalogos
from tests.conftest import id_categoria
from tests.test_web import web  # noqa: F401  (fixture)


@pytest.fixture
def ctx(con):
    return importacion.construir_contexto(con)


def _categoria(linea, ctx):
    [r] = [i for i in interpretar(["16/11", linea], ctx) if i.clase == "movimiento"]
    return r.categoria_id, r.notas


def test_clave_etiqueta():
    assert clave_etiqueta("Servicios públicos") == "serviciospublicos"
    assert clave_etiqueta("4x1000 (GMF)") == "4x1000gmf"
    assert clave_etiqueta("Café y snacks") == "cafeysnacks"


def test_cada_etiqueta_sugerida_resuelve_su_categoria(con, ctx):
    etiquetas = etiquetas_categorias(ctx.categorias)
    assert len(etiquetas) == len(catalogos.listar_categorias(con, solo_activas=True))
    assert len({(e["tipo"], e["etiqueta"]) for e in etiquetas}) == len(etiquetas)  # sin repetidas
    for e in etiquetas:
        signo = "+" if e["tipo"] == "ingreso" else ""
        cat, notas = _categoria(f"{signo}10.000 prueba #{e['etiqueta']}", ctx)
        assert cat == e["id"], (e, notas)


def test_nombres_repetidos_usan_padre(con, ctx):
    por_etiqueta = {e["etiqueta"]: e for e in etiquetas_categorias(ctx.categorias) if e["tipo"] == "ingreso"}
    assert "salario/salario" in por_etiqueta and "salario/general" in por_etiqueta
    # #salario a secas elige la subcategoría (la más específica), como antes de existir el autocompletado.
    cat, _ = _categoria("+3.000.000 pago #salario", ctx)
    assert cat == por_etiqueta["salario/salario"]["id"]


def test_etiquetas_con_espacios_y_tildes(con, ctx):
    assert _categoria("80.000 luz #serviciospublicos", ctx)[0] == id_categoria(con, "Servicios públicos")
    assert _categoria("80.000 luz #ServiciosPúblicos", ctx)[0] == id_categoria(con, "Servicios públicos")
    assert _categoria("4.000 cobro #4x1000gmf", ctx)[0] == id_categoria(con, "4x1000 (GMF)")
    assert _categoria("50.000 casa #vivienda/general", ctx)[0] == id_categoria(con, "Vivienda")
    cat, notas = _categoria("5.000 algo #noexiste/nada", ctx)
    assert cat is None and "No existe" in notas[0]


def test_paginas_incluyen_el_autocompletado(web):
    for ruta in ("/", "/movimientos", "/importar"):
        html = web.get(ruta).text
        assert "data-etiquetas" in html and "/static/etiquetas.js" in html
        datos = html.split('<script id="etiquetas-categorias" type="application/json">')[1].split("</script>")[0]
        assert any(c["etiqueta"] == "arriendo" for c in json.loads(datos))
    assert web.get("/static/etiquetas.js").status_code == 200
