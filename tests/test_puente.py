"""Puente navegador ↔ app (versión GitHub Pages): se prueba en CPython, igual que en Pyodide."""
import asyncio
from urllib.parse import urlencode

import pytest

from finanzas.web import puente

FORM = [("content-type", "application/x-www-form-urlencoded")]


@pytest.fixture
def pedir(con, monkeypatch):
    import anyio.to_thread
    monkeypatch.setenv("FINANZAS_MODO", "navegador")
    monkeypatch.setattr(anyio.to_thread, "run_sync", anyio.to_thread.run_sync)  # se restaura al terminar
    puente.sin_hilos()  # como en Pyodide: sin hilos
    puente.iniciar("https://ejemplo.github.io/finanzas/static/")

    def _pedir(metodo, url, datos=None, cabeceras=None):
        cuerpo = urlencode(datos).encode() if datos is not None else b""
        return asyncio.run(puente.peticion(metodo, url, cabeceras if cabeceras is not None else FORM, cuerpo))
    return _pedir


def test_get_pagina_y_static_reescrito(pedir):
    r = pedir("GET", "/")
    assert r["estado"] == 200 and r["tipo"].startswith("text/html")
    html = r["cuerpo"].decode()
    assert 'href="https://ejemplo.github.io/finanzas/static/app.css' in html
    assert '"/static/' not in html
    assert r["modifico"] is False


def test_post_sigue_redireccion_y_marca_cambio(pedir):
    r = pedir("POST", "/movimientos/rapido", {"texto": "45.000 almuerzo", "fecha": "2026-11-16", "volver": "/"})
    assert r["estado"] == 200
    assert r["url"].startswith("/?ok=")
    assert "Registrado: " in r["cuerpo"].decode()
    assert r["modifico"] is True
    assert "almuerzo" in pedir("GET", "/movimientos")["cuerpo"].decode()


def test_query_y_acentos(pedir):
    pedir("POST", "/configuracion/demo-cargar", {})
    r = pedir("GET", "/movimientos?tipo=gasto&q=almuerzo")
    assert r["estado"] == 200


def test_404_no_modifica(pedir):
    r = pedir("POST", "/no-existe", {})
    assert r["estado"] == 404 and r["modifico"] is False


def test_sin_respaldo_diario_en_navegador(pedir, entorno):
    assert not (entorno / "data" / "respaldos").exists() or not any((entorno / "data" / "respaldos").iterdir())


def test_reporte_pdf_se_imprime_en_navegador(pedir):
    r = pedir("GET", "/reporte.pdf")
    assert r["estado"] == 200 and r["tipo"].startswith("text/html")
    assert "setTimeout(print" in r["cuerpo"].decode()


def test_multipart(pedir):
    limite = "----limite123"
    cuerpo = (f"--{limite}\r\nContent-Disposition: form-data; name=\"texto\"\r\n\r\n"
              f"15/11 45.000 almuerzo\r\n--{limite}--\r\n").encode()
    r = asyncio.run(puente.peticion("POST", "/importar",
                                    [("content-type", f"multipart/form-data; boundary={limite}")], cuerpo))
    assert r["estado"] == 200 and "/importar/" in r["url"]


def test_exportar_descarga_en_navegador(pedir):
    pedir("POST", "/configuracion/demo-cargar", {})
    for formato, ext in (("xlsx", ".xlsx"), ("csv", ".csv")):
        r = pedir("POST", "/configuracion/exportar", {"formato": formato})
        assert r["estado"] == 200
        assert "attachment" in r["cabeceras"]["content-disposition"] and ext in r["cabeceras"]["content-disposition"]
        assert r["texto"] is None and len(r["cuerpo"]) > 100


def test_configuracion_navegador_sin_respaldos_de_disco(pedir):
    html = pedir("GET", "/configuracion")["cuerpo"].decode()
    assert "/configuracion/respaldo" not in html and 'href="/seguridad"' in html


def test_revisar_base(pedir, entorno):
    from finanzas import config
    ruta = str(config.ruta_db())
    antes = puente.revisar_base(ruta)
    assert antes["integridad"] == "ok"
    pedir("POST", "/configuracion/demo-cargar", {})
    despues = puente.revisar_base(ruta)
    assert despues["registros"] > antes["registros"]


def test_revisar_base_danada(tmp_path):
    ruta = tmp_path / "rota.db"
    ruta.write_bytes(b"esto no es sqlite" * 100)
    assert puente.revisar_base(str(ruta))["integridad"] != "ok"
