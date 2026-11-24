import sqlite3

import pytest

from finanzas import config
from finanzas.importers import importacion
from finanzas.services import movimientos
from finanzas.services.errores import ErrorValidacion
from tests.conftest import id_categoria, id_cuenta

TEXTO = """16/11
45.000 almuerzo
12k bus
12k bus
+3.200.000 salario bbva
"""


def estados(con, lote):
    return [l["estado"] for l in importacion.lineas_del_lote(con, lote)]


def test_flujo_completo_y_archivo_creciente(con):
    lote = importacion.preparar_texto(con, TEXTO)
    assert estados(con, lote) == ["lista"] * 4
    r = importacion.confirmar(con, lote)
    assert r["importados"] == 4 and r["respaldo"].exists()
    assert len(movimientos.listar(con)) == 4  # dos buses idénticos son dos movimientos

    # El mismo archivo con una línea nueva: solo entra la nueva.
    lote2 = importacion.preparar_texto(con, TEXTO + "17/11\n20k almuerzo\n")
    assert estados(con, lote2) == ["ya_importada"] * 4 + ["lista"]
    importacion.confirmar(con, lote2)
    assert len(movimientos.listar(con)) == 5

    # Reimportar todo exactamente igual: nada nuevo.
    lote3 = importacion.preparar_texto(con, TEXTO + "17/11\n20k almuerzo\n")
    with pytest.raises(ErrorValidacion, match="No hay líneas nuevas"):
        importacion.confirmar(con, lote3)


def test_indice_unico_impide_duplicar_aunque_falle_la_logica(con):
    lote = importacion.preparar_texto(con, "16/11\n45.000 almuerzo\n")
    importacion.confirmar(con, lote)
    huella = con.execute("SELECT huella FROM movimientos").fetchone()["huella"]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO movimientos (tipo, fecha, valor, descripcion, cuenta_id, categoria_id, origen, lote_id, huella) "
            "VALUES ('gasto', '2026-11-16', 45000, 'x', 1, ?, 'importacion', ?, ?)",
            (id_categoria(con, "Mercado"), lote, huella),
        )


def test_lineas_con_problemas_bloquean_la_confirmacion(con):
    lote = importacion.preparar_texto(con, "16/11\n45.000 cosa rara xyz\n12k bus\n")
    assert estados(con, lote) == ["revisar", "lista"]
    with pytest.raises(ErrorValidacion, match="por revisar"):
        importacion.confirmar(con, lote)
    assert movimientos.listar(con) == []  # nada se registró

    importacion.corregir(con, lote, 2, {"categoria_id": id_categoria(con, "Varios")}, recordar_patron="cosa rara")
    assert estados(con, lote) == ["lista", "lista"]
    importacion.confirmar(con, lote)
    # La corrección se aprendió como regla.
    assert con.execute("SELECT 1 FROM reglas_clasificacion WHERE patron = 'cosa rara'").fetchone()


def test_posible_duplicado_con_registro_manual(con):
    movimientos.crear(con, movimientos.DatosMovimiento(
        tipo="gasto", fecha="2026-11-16", valor=45_000, descripcion="Almuerzo con Ana",
        cuenta_id=id_cuenta(con, "Nequi"), categoria_id=id_categoria(con, "Restaurantes y almuerzos")))
    lote = importacion.preparar_texto(con, "16/11\n45.000 almuerzo\n")
    (linea,) = importacion.lineas_del_lote(con, lote)
    assert linea["estado"] == "revisar"
    assert "Posible duplicado" in linea["problemas"][0]["texto"]
    # El usuario decide: descartar (era el mismo) o aceptar (eran dos).
    importacion.corregir(con, lote, 2, descartar=True)
    assert estados(con, lote) == ["descartada"]


def test_linea_editada_despues_de_importar(con):
    lote = importacion.preparar_texto(con, "16/11\n45.000 almuerzo\n")
    importacion.confirmar(con, lote)
    lote2 = importacion.preparar_texto(con, "16/11\n48.000 almuerzo\n")  # el usuario cambió el valor
    (linea,) = importacion.lineas_del_lote(con, lote2)
    assert linea["estado"] == "revisar" and "Modificaste" in linea["problemas"][0]["texto"]


def test_aceptar_aviso_permite_importar(con):
    lote = importacion.preparar_texto(con, "16/11\n500 bus\n")
    assert estados(con, lote) == ["revisar"]
    importacion.corregir(con, lote, 2, aceptar_avisos=True)
    assert estados(con, lote) == ["lista"]


def test_fechas_invalidas(con):
    lote = importacion.preparar_texto(con, "10/11\n45.000 almuerzo\n25/11\n10k bus\n")
    lineas = importacion.lineas_del_lote(con, lote)
    assert "anterior al inicio" in lineas[0]["problemas"][0]["texto"]
    assert lineas[1]["fecha"] == "2026-11-25" and lineas[1]["estado"] == "lista"  # programado


def test_revertir_y_volver_a_importar(con):
    lote = importacion.preparar_texto(con, TEXTO)
    importacion.confirmar(con, lote)
    assert importacion.revertir(con, lote) == 4
    assert movimientos.listar(con) == []
    assert len(movimientos.listar(con, incluir_eliminados=True)) == 4  # se conservan
    lote2 = importacion.preparar_texto(con, TEXTO)
    assert estados(con, lote2) == ["lista"] * 4


def test_original_archivado(con):
    lote = importacion.preparar_texto(con, TEXTO)
    fila = con.execute("SELECT ruta_copia FROM lotes_importacion WHERE id = ?", (lote,)).fetchone()
    from pathlib import Path
    ruta = Path(fila["ruta_copia"])
    assert ruta.exists() and ruta.read_text(encoding="utf-8") == TEXTO
    assert config.dir_importaciones() in ruta.parents


def test_docx(con, tmp_path):
    from docx import Document
    doc = Document()
    doc.add_paragraph("Movimientos noviembre 2026")
    doc.add_paragraph("16/11")
    doc.add_paragraph("45.000 almuerzo", style="List Bullet")  # Word convierte '-' en viñeta
    p = doc.add_paragraph("12k bus")
    p.add_run().add_break()
    p.add_run("+3.200.000 salario bbva")
    ruta = tmp_path / "Movimientos 2026-11.docx"
    doc.save(ruta)
    lote = importacion.preparar_archivo(con, ruta)
    lineas = importacion.lineas_del_lote(con, lote)
    assert [l["estado"] for l in lineas] == ["ignorada", "lista", "lista", "lista"]
    assert [l["valor"] for l in lineas[1:]] == [45_000, 12_000, 3_200_000]


def test_lote_confirmado_no_se_modifica(con):
    lote = importacion.preparar_texto(con, TEXTO)
    importacion.confirmar(con, lote)
    with pytest.raises(ErrorValidacion):
        importacion.corregir(con, lote, 2, {"valor": 1})
    with pytest.raises(ErrorValidacion):
        importacion.confirmar(con, lote)
