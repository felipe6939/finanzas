"""Arma la versión navegador (GitHub Pages) en `sitio/`.

    .venv\\Scripts\\python.exe navegador\\construir.py

Todo queda alojado en el propio sitio (sin CDN): Pyodide, sus paquetes, las librerías
puras de PyPI y el paquete `finanzas` comprimido. Cada descarga se verifica con su sha256
y se guarda en `navegador/.cache` para no repetirla.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NAV = RAIZ / "navegador"
CACHE = NAV / ".cache"
SITIO = RAIZ / "sitio"

PYODIDE = "314.0.3"  # Python 3.14, igual que el PC
CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE}/full/"
ARCHIVOS_BASE = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]
# Paquetes que la app necesita de Pyodide (se agregan sus dependencias).
PAQUETES = ["fastapi", "jinja2", "lxml", "typing-extensions"]
# Librerías 100 % Python que Pyodide no trae: se bajan de PyPI con la versión fija del PC.
PYPI = {"python-multipart": "0.0.33", "python-docx": "1.2.0", "openpyxl": "3.1.5", "et-xmlfile": "2.0.0"}
PROPIOS = ["index.html", "motor.js", "boveda.js", "sw.js", "manifest.webmanifest"]


def _normal(nombre: str) -> str:
    return nombre.lower().replace("_", "-").replace(".", "-")


def _bajar(url: str, destino: Path, sha256: str | None = None) -> Path:
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        print(f"  bajando {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "finanzas-construir"})
        with urllib.request.urlopen(req, timeout=120) as r:
            datos = r.read()
        tmp = destino.with_suffix(destino.suffix + ".tmp")
        tmp.write_bytes(datos)
        tmp.replace(destino)
    if sha256 and hashlib.sha256(destino.read_bytes()).hexdigest() != sha256:
        destino.unlink()
        raise SystemExit(f"Huella sha256 incorrecta: {url}")
    return destino


def _dependencias(lock: dict) -> list[dict]:
    paquetes = {_normal(k): v for k, v in lock["packages"].items()}
    vistos: dict[str, dict] = {}

    def agregar(n: str):
        n = _normal(n)
        if n in vistos:
            return
        vistos[n] = paquetes[n]
        for d in paquetes[n]["depends"]:
            agregar(d)
    for p in PAQUETES:
        agregar(p)
    return list(vistos.values())


def _wheel_pypi(nombre: str, version: str) -> tuple[str, str, str]:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{nombre}/{version}/json", timeout=60) as r:
        info = json.load(r)
    for f in info["urls"]:
        if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl"):
            return f["filename"], f["url"], f["digests"]["sha256"]
    raise SystemExit(f"{nombre} {version}: no hay wheel puro en PyPI")


def _zip_finanzas(destino: Path) -> None:
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((RAIZ / "finanzas").rglob("*")):
            rel = p.relative_to(RAIZ)
            if p.is_dir() or "__pycache__" in rel.parts or p.suffix == ".pyc":
                continue
            if rel.parts[:3] == ("finanzas", "web", "static"):
                continue  # static se publica como archivos sueltos
            z.write(p, rel.as_posix())


def construir() -> Path:
    if SITIO.exists():
        shutil.rmtree(SITIO)
    (SITIO / "pyodide").mkdir(parents=True)
    (SITIO / "wheels").mkdir()
    (SITIO / "app").mkdir()

    print(f"Pyodide {PYODIDE}")
    base = CACHE / f"pyodide-{PYODIDE}"
    for f in ARCHIVOS_BASE:
        shutil.copy2(_bajar(CDN + f, base / f), SITIO / "pyodide" / f)
    lock = json.loads((SITIO / "pyodide" / "pyodide-lock.json").read_text(encoding="utf-8"))
    for p in _dependencias(lock):
        shutil.copy2(_bajar(CDN + p["file_name"], base / p["file_name"], p["sha256"]), SITIO / "pyodide" / p["file_name"])

    print("PyPI")
    ruedas = []
    for nombre, version in PYPI.items():
        archivo, url, sha = _wheel_pypi(nombre, version)
        shutil.copy2(_bajar(url, CACHE / "pypi" / archivo, sha), SITIO / "wheels" / archivo)
        ruedas.append(f"wheels/{archivo}")

    print("App")
    shutil.copytree(RAIZ / "finanzas" / "web" / "static", SITIO / "static",
                    ignore=shutil.ignore_patterns("__pycache__"))
    _zip_finanzas(SITIO / "app" / "finanzas.zip")
    for f in PROPIOS:
        shutil.copy2(NAV / f, SITIO / f)
    (SITIO / ".nojekyll").write_text("")  # GitHub Pages: servir todo tal cual

    # Versión = huella de todo el contenido: el service worker renueva su caché cuando cambia.
    archivos = sorted(p for p in SITIO.rglob("*") if p.is_file() and p.name not in ("sw.js", ".nojekyll"))
    h = hashlib.sha256()
    for p in archivos:
        h.update(p.relative_to(SITIO).as_posix().encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    version = h.hexdigest()[:12]
    from finanzas import __version__
    rutas = [p.relative_to(SITIO).as_posix() for p in archivos]
    (SITIO / "app" / "manifiesto.json").write_text(json.dumps(
        {"version": version, "app": __version__, "pyodide": PYODIDE, "paquetes": PAQUETES, "ruedas": ruedas},
        ensure_ascii=False, indent=1), encoding="utf-8")
    rutas.append("app/manifiesto.json")
    sw = (SITIO / "sw.js").read_text(encoding="utf-8")
    sw = sw.replace("__VERSION__", version).replace("__ARCHIVOS__", json.dumps(["./"] + rutas))
    (SITIO / "sw.js").write_text(sw, encoding="utf-8")

    total = sum(p.stat().st_size for p in SITIO.rglob("*") if p.is_file())
    print(f"Listo: {SITIO}  ·  versión {version}  ·  {total / 1e6:.1f} MB")
    return SITIO


if __name__ == "__main__":
    sys.path.insert(0, str(RAIZ))
    construir()
