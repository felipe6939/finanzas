"""Sincronización con GitHub (navegador/nube.js), probada en Node con un GitHub simulado."""
import shutil
import subprocess
from pathlib import Path

import pytest

PRUEBA = Path(__file__).resolve().parent.parent / "navegador" / "pruebas" / "prueba_nube.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="requiere Node.js")
def test_sincronizacion_nube():
    r = subprocess.run(["node", str(PRUEBA)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TODO BIEN" in r.stdout
