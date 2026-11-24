import sqlite3

import pytest

from finanzas import config, demo, respaldo
from finanzas.analytics import resumen
from finanzas.core.fechas import Mes
from finanzas.core.formato import cop, cop_corto, fecha_corta, porcentaje
from finanzas.db.conexion import conectar
from finanzas.db.migrar import VERSION_ACTUAL, migrar
from finanzas.services import catalogos, movimientos
from finanzas.services.errores import ErrorValidacion
from tests.conftest import id_categoria, id_cuenta


def gasto(con, valor=45_000, fecha="2026-11-16", desc="Almuerzo", cat="Restaurantes y almuerzos"):
    return movimientos.crear(con, movimientos.DatosMovimiento(
        tipo="gasto", fecha=fecha, valor=valor, descripcion=desc,
        cuenta_id=id_cuenta(con, "Nequi"), categoria_id=id_categoria(con, cat)))


def test_formato_colombiano():
    assert cop(1_250_000) == "$ 1.250.000"
    assert cop(-45_000) == "−$ 45.000"
    assert cop(45_000, signo=True) == "+$ 45.000"
    assert cop_corto(1_250_000) == "$ 1,3 M" and cop_corto(45_000) == "$ 45 mil"
    assert fecha_corta("2026-11-16") == "16 nov 2026"
    assert porcentaje(0.172) == "17 %"


def test_migracion_idempotente(con):
    assert migrar(con) == []
    assert con.execute("PRAGMA user_version").fetchone()[0] == VERSION_ACTUAL


def test_validaciones(con):
    casos = [
        ({"valor": 0}, "mayor que cero"),
        ({"valor": -5}, "mayor que cero"),
        ({"fecha": "2026-11-15"}, "anterior al inicio"),
        ({"fecha": "2027-12-01"}, "más de un año"),
        ({"fecha": "2026-13-01"}, "no es válida"),
        ({"descripcion": "  "}, "descripción"),
        ({"categoria_id": None}, "categoría"),
        ({"categoria_id": "ingreso"}, "no es de tipo"),
    ]
    for cambios, mensaje in casos:
        datos = dict(tipo="gasto", fecha="2026-11-16", valor=1000, descripcion="x",
                     cuenta_id=id_cuenta(con, "Nequi"), categoria_id=id_categoria(con, "Mercado"))
        if cambios.get("categoria_id") == "ingreso":
            cambios = {"categoria_id": id_categoria(con, "Salario", "ingreso")}
        datos.update(cambios)
        with pytest.raises(ErrorValidacion, match=mensaje):
            movimientos.crear(con, movimientos.DatosMovimiento(**datos))


def test_clasificacion_por_defecto_desde_la_categoria(con):
    m = movimientos.obtener(con, gasto(con, cat="Arriendo"))
    assert (m["naturaleza"], m["necesidad"]) == ("fijo", "esencial")


def test_borrado_logico_auditado_y_borrado_fisico_bloqueado(con):
    mid = gasto(con)
    movimientos.actualizar(con, mid, movimientos.DatosMovimiento(
        tipo="gasto", fecha="2026-11-16", valor=50_000, descripcion="Almuerzo",
        cuenta_id=id_cuenta(con, "Nequi"), categoria_id=id_categoria(con, "Restaurantes y almuerzos")))
    movimientos.eliminar(con, mid)
    acciones = [h["accion"] for h in movimientos.historial(con, mid)]
    assert acciones == ["crear", "modificar", "eliminar"]
    assert movimientos.listar(con) == []
    with pytest.raises(sqlite3.IntegrityError, match="no se borran"):
        con.execute("DELETE FROM movimientos WHERE id = ?", (mid,))
    movimientos.restaurar(con, mid)
    assert len(movimientos.listar(con)) == 1


def test_restriccion_de_valor_en_la_base(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO movimientos (tipo, fecha, valor, descripcion, cuenta_id, categoria_id, origen) "
                    "VALUES ('gasto', '2026-11-16', -1, 'x', 1, 1, 'manual')")


def test_respaldo_y_restauracion(con):
    gasto(con)
    copia = respaldo.crear_respaldo(con, "prueba")
    gasto(con, valor=99_000, desc="Otro")
    con.close()
    seguridad = respaldo.restaurar(copia)
    con2 = conectar()
    assert [m["valor"] for m in movimientos.listar(con2)] == [45_000]
    assert seguridad.exists()
    con2.close()


def test_rotacion_de_respaldos(con):
    for _ in range(35):
        respaldo.crear_respaldo(con, "x")
    assert len(respaldo.listar_respaldos()) <= respaldo.CONSERVAR_RECIENTES + 1


def test_exportar(con):
    gasto(con)
    for formato in ("xlsx", "csv"):
        ruta = respaldo.exportar(con, formato)
        assert ruta.exists() and config.dir_exportaciones() in ruta.parents


def test_demo_se_elimina_sin_rastro(con):
    saldo_antes = catalogos.saldo_inicial_total(con)
    n = demo.cargar(con)
    assert n > 100 and demo.demo_activo(con)
    assert catalogos.fecha_inicio(con).isoformat() == "2026-09-01"
    assert demo.eliminar(con) == n
    assert con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE tabla = 'movimientos'").fetchone()[0] == 0
    assert catalogos.fecha_inicio(con).isoformat() == "2026-11-16"
    assert catalogos.saldo_inicial_total(con) == saldo_antes


def test_reiniciar(con):
    from finanzas.importers import importacion
    demo.cargar(con)
    lote = importacion.preparar_texto(con, "16/11\n45.000 almuerzo\n")
    importacion.confirmar(con, lote)
    r = demo.reiniciar(con)
    assert r["respaldo"].exists()
    assert con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM lotes_importacion").fetchone()[0] == 0
    assert not demo.demo_activo(con)
    assert catalogos.fecha_inicio(con).isoformat() == "2026-11-16"
    # La protección contra borrado físico vuelve a quedar activa.
    gasto(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM movimientos")


def test_alias_de_cuenta_no_se_repite(con):
    with pytest.raises(ErrorValidacion, match="ya lo usa"):
        catalogos.guardar_cuenta(con, None, "Nequi 2", "billetera", "nequi", 0)


def test_metricas(con):
    catalogos.guardar_cuenta(con, id_cuenta(con, "Nequi"), "Nequi", "billetera", "nequi", 1_000_000)
    movimientos.crear(con, movimientos.DatosMovimiento(
        tipo="ingreso", fecha="2026-11-16", valor=4_000_000, descripcion="Salario",
        cuenta_id=id_cuenta(con, "BBVA"), categoria_id=id_categoria(con, "Salario", "ingreso")))
    gasto(con, valor=1_000_000, cat="Arriendo", desc="Arriendo")
    gasto(con, valor=500_000, cat="Mercado", desc="Mercado")
    movimientos.crear(con, movimientos.DatosMovimiento(
        tipo="ahorro", fecha="2026-11-17", valor=400_000, descripcion="Ahorro",
        cuenta_id=id_cuenta(con, "Bancolombia")))
    k = resumen.kpis(con, Mes(2026, 11))
    a = k["actual"]
    assert (a["ingresos"], a["gastos"], a["ahorro"]) == (4_000_000, 1_500_000, 400_000)
    assert a["balance"] == 2_500_000 and a["sobrante"] == 2_100_000
    assert a["tasa_ahorro"] == pytest.approx(0.10)
    assert k["posicion"] == {"patrimonio": 3_500_000, "ahorro_acumulado": 400_000, "disponible": 3_100_000,
                              "me_deben": 0}
    cats = resumen.gastos_por_categoria(con, Mes(2026, 11))
    assert [c["nombre"] for c in cats] == ["Vivienda", "Alimentación"]
    assert cats[0]["participacion"] == pytest.approx(2 / 3)
    assert {d["clave"] for d in resumen.distribucion(con, Mes(2026, 11), "naturaleza")} == {"fijo", "variable"}


def test_comparacion_mes_en_curso_es_al_mismo_dia(con, monkeypatch):
    demo.cargar(con)
    k = resumen.kpis(con, Mes(2026, 11))  # hoy = 20 nov
    assert k["parcial"]
    assert k["periodo_comparacion"].hasta.isoformat() == "2026-10-20"
    k_oct = resumen.kpis(con, Mes(2026, 10))
    assert not k_oct["parcial"] and k_oct["periodo_comparacion"].hasta.isoformat() == "2026-09-30"
    obs = resumen.insights(con, Mes(2026, 11))
    assert any(o["clase"] == "proyeccion" for o in obs)
    assert all("texto" in o for o in obs)
