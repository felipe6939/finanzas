"""Gastos fijos mensuales: se registran al marcar la casilla, con la fecha de ese día, una vez por mes."""
import pytest

from finanzas.analytics import resumen
from finanzas.core.fechas import Mes
from finanzas.services import gastos_fijos, movimientos
from finanzas.services.errores import ErrorValidacion
from tests.conftest import id_categoria, id_cuenta
from tests.test_web import web  # noqa: F401  (fixture)

# conftest: hoy = 20 nov 2026, inicio del sistema = 16 nov 2026


def _arriendo(con):
    return next(g for g in gastos_fijos.listar(con) if g["descripcion"] == "Arriendo")


def _gastos(con, cat="Arriendo"):
    return movimientos.listar(con, categoria_id=id_categoria(con, cat))


def test_ejemplo_arriendo_no_registra_nada_solo(con):
    g = _arriendo(con)
    assert g["valor"] == 550_000 and g["categoria_completa"] == "Vivienda › Arriendo"
    assert g["este_mes"] is None and _gastos(con) == []


def test_marcar_registra_hoy_en_su_categoria_y_cuenta(con):
    g = _arriendo(con)
    gastos_fijos.guardar(con, g["id"], "Arriendo", 550_000, g["categoria_id"], id_cuenta(con, "Bancolombia"))
    msg = gastos_fijos.registrar_mes(con, g["id"])
    assert "Registrado: Arriendo $ 550.000 · Bancolombia · 20 nov 2026" in msg
    [m] = _gastos(con)
    assert m["fecha"] == "2026-11-20" and m["valor"] == 550_000 and m["cuenta"] == "Bancolombia"
    assert m["categoria"] == "Arriendo" and m["naturaleza"] == "fijo"
    assert _arriendo(con)["este_mes"]["movimiento_id"] == m["id"]
    assert resumen.kpis(con, Mes(2026, 11))["actual"]["gastos"] == 550_000


def test_no_deja_registrar_dos_veces_en_el_mes(con, monkeypatch):
    g = _arriendo(con)
    gastos_fijos.registrar_mes(con, g["id"])
    monkeypatch.setenv("FINANZAS_HOY", "2026-11-29")
    with pytest.raises(ErrorValidacion, match="ya está registrado este mes"):
        gastos_fijos.registrar_mes(con, g["id"])
    assert len(_gastos(con)) == 1

    # El mes siguiente la casilla vuelve a quedar libre.
    monkeypatch.setenv("FINANZAS_HOY", "2026-12-03")
    assert _arriendo(con)["este_mes"] is None
    gastos_fijos.registrar_mes(con, g["id"])
    assert [m["fecha"] for m in _gastos(con)] == ["2026-12-03", "2026-11-20"]


def test_si_ya_estaba_registrado_a_mano_no_duplica(con):
    cat = id_categoria(con, "Arriendo")
    manual = movimientos.crear(con, movimientos.DatosMovimiento(
        tipo="gasto", fecha="2026-11-18", valor=550_000, descripcion="arriendo noviembre",
        cuenta_id=id_cuenta(con, "Nequi"), categoria_id=cat))
    msg = gastos_fijos.registrar_mes(con, _arriendo(con)["id"])
    assert "ya estaba registrado" in msg
    assert len(_gastos(con)) == 1 and _arriendo(con)["este_mes"]["movimiento_id"] == manual


def test_deshacer_y_eliminar_a_mano_liberan_la_casilla(con):
    g = _arriendo(con)
    gastos_fijos.registrar_mes(con, g["id"])
    gastos_fijos.deshacer_mes(con, g["id"])
    assert _gastos(con) == [] and _arriendo(con)["este_mes"] is None
    gastos_fijos.registrar_mes(con, g["id"])  # se puede volver a registrar
    [m] = _gastos(con)
    movimientos.eliminar(con, m["id"])
    assert _arriendo(con)["este_mes"] is None
    gastos_fijos.registrar_mes(con, g["id"])
    assert len(_gastos(con)) == 1
    with pytest.raises(ErrorValidacion):
        gastos_fijos.registrar_mes(con, g["id"])


def test_validaciones(con):
    with pytest.raises(ErrorValidacion) as e:
        gastos_fijos.guardar(con, None, "", 0, id_categoria(con, "Salario", "ingreso"), None)
    texto = " ".join(e.value.errores)
    assert "descripción" in texto and "mayor que cero" in texto and "de gasto" in texto and "cuenta" in texto
    g = _arriendo(con)
    gastos_fijos.eliminar(con, g["id"])
    assert gastos_fijos.listar(con) == []
    with pytest.raises(ErrorValidacion):
        gastos_fijos.registrar_mes(con, g["id"])
    with pytest.raises(ErrorValidacion):
        gastos_fijos.deshacer_mes(con, g["id"])


def test_reiniciar_con_gastos_fijos(con):
    from finanzas import demo
    gastos_fijos.registrar_mes(con, _arriendo(con)["id"])
    demo.reiniciar(con)  # no falla por el vínculo con el movimiento borrado
    assert _gastos(con) == [] and _arriendo(con)["este_mes"] is None


def test_web_gastos_fijos(web):
    pagina = web.get("/movimientos/fijos").text
    assert "Gastos fijos mensuales" in pagina and "Arriendo" in pagina and "550.000" in pagina
    assert "Gastos fijos mensuales" in web.get("/movimientos").text  # pestaña en Movimientos

    r = web.post("/movimientos/fijos/guardar",
                 data={"descripcion": "Gimnasio", "valor": "80.000", "categoria_id": "", "cuenta_id": "1"})
    assert "Falta la categoría" in r.text

    gid = pagina.split('action="/movimientos/fijos/registrar"')[1].split('value="')[1].split('"')[0]
    r = web.post("/movimientos/fijos/registrar", data={"id": gid})
    assert "Registrado: Arriendo" in r.text and "casilla-pago marcada" in r.text
    assert 'action="/movimientos/fijos/registrar"' not in r.text  # ya no hay casilla para marcar

    r = web.post("/movimientos/fijos/registrar", data={"id": gid})  # p. ej. doble clic
    assert "ya está registrado este mes" in r.text
    assert web.get("/movimientos").text.count("Gasto fijo mensual") == 1

    r = web.post("/movimientos/fijos/deshacer", data={"id": gid})
    assert "Se deshizo" in r.text and 'action="/movimientos/fijos/registrar"' in r.text
