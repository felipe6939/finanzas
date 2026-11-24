"""Auditoría del sitio público antes de publicarlo: que no vaya ningún dato del usuario.

    python navegador/auditar.py sitio

Falla (código 1) si encuentra bases de datos, copias, exportaciones, importaciones o
rastros personales, incluso dentro de app/finanzas.zip.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

EXTENSIONES_PROHIBIDAS = {".db", ".sqlite", ".sqlite3", ".db-journal", ".cifrado", ".xlsx", ".csv",
                          ".docx", ".pdf", ".bak", ".env", ".pem", ".key"}
CARPETAS_PROHIBIDAS = {"data", "datos", "respaldos", "importaciones", "exportaciones", "informes", ".claude"}
RASTROS = re.compile(rb"jualozfe|lozanofernandez|C:\\\\Users|github_pat_|ghp_[A-Za-z0-9]{20}", re.I)
TEXTO = {".html", ".js", ".mjs", ".css", ".json", ".webmanifest", ".py", ".txt", ".md"}


def problemas_de(nombre: str, contenido: bytes | None) -> list[str]:
    ruta = Path(nombre)
    salida = []
    if ruta.suffix.lower() in EXTENSIONES_PROHIBIDAS:
        salida.append(f"{nombre}: tipo de archivo de datos")
    if CARPETAS_PROHIBIDAS & {p.lower() for p in ruta.parts[:-1]}:
        salida.append(f"{nombre}: carpeta de datos")
    if contenido is not None and ruta.suffix.lower() in TEXTO and RASTROS.search(contenido):
        salida.append(f"{nombre}: contiene un rastro personal o un token")
    return salida


def auditar(sitio: Path) -> list[str]:
    problemas = []
    for archivo in sorted(p for p in sitio.rglob("*") if p.is_file()):
        rel = archivo.relative_to(sitio).as_posix()
        problemas += problemas_de(rel, archivo.read_bytes() if archivo.suffix.lower() in TEXTO else None)
        if archivo.suffix == ".zip" and archivo.parent.name == "app":
            with zipfile.ZipFile(archivo) as z:
                for info in z.infolist():
                    problemas += problemas_de(f"{rel}!{info.filename}", z.read(info))
    return problemas


def main() -> int:
    sitio = Path(sys.argv[1] if len(sys.argv) > 1 else "sitio")
    if not (sitio / "index.html").exists():
        print(f"No encontré el sitio en {sitio}")
        return 1
    problemas = auditar(sitio)
    for p in problemas:
        print("PROBLEMA:", p)
    print("Auditoría:", "FALLÓ" if problemas else "sin datos ni rastros personales")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
