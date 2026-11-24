"""Flujo que usa la Skill: preparar → ver pendientes → corregir → confirmar, solo por CLI."""
import json

from finanzas.analytics import resumen
from finanzas.cli import main
from finanzas.core.fechas import Mes
from finanzas.importers import importacion
from finanzas.services import movimientos


def test_flujo_skill_por_cli(con, tmp_path, capsys):
    archivo = tmp_path / "Movimientos 2026-11.txt"
    archivo.write_text("16/11\n2 almuerzos 30.000\n38.000 cosa rara xyz\n12k bus\n", encoding="utf-8")
    assert main(["importar", "preparar", str(archivo)]) == 0
    capsys.readouterr()

    assert main(["importar", "ver", "1", "--pendientes", "--json"]) == 0
    datos = json.loads(capsys.readouterr().out)
    assert [l["num_linea"] for l in datos["lineas"]] == [3]  # solo lo ambiguo
    assert not datos["puede_confirmar"]

    assert main(["importar", "confirmar", "1"]) == 2  # bloqueado: hay pendientes
    assert movimientos.listar(con) == []

    assert main(["importar", "corregir", "1", "3", "--categoria", "Otros gastos › Varios",
                 "--recordar", "cosa rara"]) == 0
    assert main(["importar", "confirmar", "1"]) == 0
    assert len(movimientos.listar(con)) == 3
    capsys.readouterr()

    # Reimportar el archivo creciente: solo entra lo nuevo y la regla aprendida clasifica sola.
    archivo.write_text(archivo.read_text(encoding="utf-8") + "17/11\n20.000 cosa rara\n", encoding="utf-8")
    assert main(["importar", "preparar", str(archivo)]) == 0
    r = importacion.resumen_lote(con, 2)
    assert r["conteo"]["ya_importada"] == 3 and r["conteo"]["lista"] == 1 and r["puede_confirmar"]


def test_categoria_inexistente_da_error_claro(con, tmp_path, capsys):
    archivo = tmp_path / "x.txt"
    archivo.write_text("16/11\n38.000 cosa rara\n", encoding="utf-8")
    main(["importar", "preparar", str(archivo)])
    assert main(["importar", "corregir", "1", "2", "--categoria", "Inventada"]) == 2
    assert "no encontrada" in capsys.readouterr().err


def test_proyeccion_solo_extrapola_variables(con):
    from finanzas import demo
    demo.cargar(con)  # hoy = 20 nov (conftest)
    obs = [o for o in resumen.insights(con, Mes(2026, 11)) if o["clase"] == "proyeccion"]
    assert len(obs) == 1 and "variables" in obs[0]["texto"] and obs[0]["metodo"]
    nat = {d["clave"]: d["total"] for d in resumen.distribucion(con, Mes(2026, 11), "naturaleza")}
    esperado = nat.get("fijo", 0) + nat.get("extraordinario", 0) + round(nat["variable"] / 20 * 30)
    assert f"{esperado:,}".replace(",", ".") in obs[0]["texto"]
