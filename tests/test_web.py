import pytest
from fastapi.testclient import TestClient

from finanzas.web.app import crear_app, leer_valor


@pytest.fixture
def web(con):
    with TestClient(crear_app()) as cliente:
        yield cliente


@pytest.mark.parametrize("texto,valor", [
    ("45.000", 45_000), ("45000", 45_000), ("45k", 45_000), ("45 mil", 45_000),
    ("$ 1.200.000", 1_200_000), ("1 200 000", 1_200_000), ("abc", None), ("", None),
])
def test_leer_valor(texto, valor):
    assert leer_valor(texto) == valor


def test_paginas_vacias(web):
    for url in ("/", "/movimientos", "/importar", "/configuracion", "/movimientos/nuevo"):
        r = web.get(url)
        assert r.status_code == 200, url


def test_paginas_con_demo(web):
    assert web.post("/configuracion/demo-cargar").status_code == 200
    r = web.get("/")
    assert "datos de ejemplo" in r.text and "g-evolucion" in r.text
    for url in ("/?mes=2026-10", "/?mes=2026-09", "/?mes=2025-01", "/?mes=basura",
                "/movimientos?todos=1", "/movimientos?tipo=gasto&categoria=1&q=almuerzo",
                "/movimientos/1", "/configuracion"):
        assert web.get(url).status_code == 200, url
    web.post("/configuracion/demo-eliminar")
    assert "datos de ejemplo (ficticios)" not in web.get("/").text


def test_registro_rapido(web):
    r = web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-16", "volver": "/"})
    assert r.status_code == 200 and "Registrado: Almuerzo $ 45.000" in r.text
    # Incompleto: se muestra el formulario con el problema.
    r = web.post("/movimientos/rapido", data={"texto": "45.000 cosa rara", "fecha": "2026-11-16"})
    assert "Falta la categoría" in r.text
    # Posible duplicado: se pide confirmación.
    r = web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-16"})
    assert "Ya hay un movimiento parecido" in r.text


def test_flujo_importacion_web(web):
    r = web.post("/importar", data={"texto": "16/11\n45.000 almuerzo\n12k cosa xyz\n"})
    assert r.status_code == 200 and "Importación #1" in r.text and "Falta la categoría" in r.text
    r = web.post("/importar/1/confirmar")
    assert "por revisar" in r.text
    r = web.post("/importar/1/linea/3", data={"tipo": "gasto", "fecha": "2026-11-16", "valor": "12.000",
                                               "descripcion": "Cosa xyz", "cuenta_id": "1",
                                               "categoria_id": "1", "recordar": "xyz"})
    assert r.status_code == 200
    r = web.post("/importar/1/confirmar")
    assert "Importados 2 movimientos" in r.text
    r = web.post("/importar", files={"archivo": ("x.txt", b"16/11\n45.000 almuerzo\n12k cosa xyz\n", "text/plain")},
                 data={"texto": ""})
    assert "Ya importadas (2)" in r.text
    r = web.post("/importar/1/revertir")
    assert "revertido: 2 movimientos" in r.text


def test_origen_externo_bloqueado(web):
    r = web.post("/configuracion/respaldo", headers={"Origin": "https://sitio-malicioso.com"})
    assert r.status_code == 403


def test_editar_y_eliminar(web):
    web.post("/movimientos/rapido", data={"texto": "45.000 almuerzo", "fecha": "2026-11-16"})
    r = web.post("/movimientos/1", data={"tipo": "gasto", "fecha": "2026-11-16", "valor": "50.000",
                                          "descripcion": "Almuerzo", "cuenta_id": "1", "categoria_id": "15"})
    assert r.status_code == 200
    r = web.get("/movimientos/1")
    assert "Historial de cambios" in r.text and "45000 → 50000" in r.text
    r = web.post("/movimientos/1/eliminar", data={"volver": "/movimientos"})
    assert "eliminado" in r.text
    assert "Eliminado el" in web.get("/movimientos/1").text


def test_punto_de_partida(web):
    r = web.post("/configuracion/inicio", data={"fecha_inicio": "2026-11-16", "saldo_1": "350.000",
                                                 "saldo_2": "2.000.000", "saldo_3": "0", "saldo_4": "50k",
                                                 "ahorro_inicial": "500.000"})
    assert "Punto de partida guardado" in r.text
    r = web.get("/configuracion")
    assert 'value="350.000"' in r.text and 'value="500.000"' in r.text
