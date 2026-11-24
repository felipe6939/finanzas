"""Las pruebas usan una carpeta temporal: nunca tocan data/ ni la base real."""
import pytest

from finanzas.db.conexion import conectar
from finanzas.db.migrar import migrar


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANZAS_DATOS", str(tmp_path / "data"))
    monkeypatch.setenv("FINANZAS_INFORMES", str(tmp_path / "informes"))
    monkeypatch.setenv("FINANZAS_HOY", "2026-11-20")
    return tmp_path


@pytest.fixture
def con():
    c = conectar()
    migrar(c)
    yield c
    c.close()


def id_cuenta(con, nombre):
    return con.execute("SELECT id FROM cuentas WHERE nombre = ?", (nombre,)).fetchone()["id"]


def id_categoria(con, nombre, tipo="gasto"):
    return con.execute(
        "SELECT id FROM categorias WHERE nombre = ? AND tipo = ? ORDER BY padre_id IS NULL LIMIT 1",
        (nombre, tipo),
    ).fetchone()["id"]
