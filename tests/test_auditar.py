"""La auditoría del sitio público detecta datos y rastros personales."""
import zipfile

from navegador.auditar import auditar


def _sitio(tmp_path):
    s = tmp_path / "sitio"
    (s / "app").mkdir(parents=True)
    (s / "index.html").write_text("<html></html>")
    with zipfile.ZipFile(s / "app" / "finanzas.zip", "w") as z:
        z.writestr("finanzas/__init__.py", "")
    return s


def test_sitio_limpio(tmp_path):
    assert auditar(_sitio(tmp_path)) == []


def test_detecta_datos_y_rastros(tmp_path):
    s = _sitio(tmp_path)
    (s / "finanzas.db").write_bytes(b"x")
    (s / "copia.cifrado").write_bytes(b"x")
    (s / "motor.js").write_text("const t = 'github_pat_abc';")
    with zipfile.ZipFile(s / "app" / "finanzas.zip", "a") as z:
        z.writestr("data/importaciones/gastos.txt", "45.000 almuerzo")
    problemas = " ".join(auditar(s))
    for esperado in ("finanzas.db", "copia.cifrado", "motor.js", "data/importaciones"):
        assert esperado in problemas
