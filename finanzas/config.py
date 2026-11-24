"""Rutas del sistema.

Los datos viven separados del código. Las variables de entorno permiten que las
pruebas usen una carpeta temporal y nunca toquen la base de datos real.
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

HOST = "127.0.0.1"  # solo accesible desde este computador
PUERTO = 8765


def modo_navegador() -> bool:
    """True cuando la app corre dentro del navegador (Pyodide, versión GitHub Pages)."""
    return os.environ.get("FINANZAS_MODO") == "navegador"


def dir_datos() -> Path:
    return Path(os.environ.get("FINANZAS_DATOS", RAIZ / "data"))


def ruta_db() -> Path:
    return dir_datos() / "finanzas.db"


def dir_respaldos() -> Path:
    return dir_datos() / "respaldos"


def dir_importaciones() -> Path:
    return dir_datos() / "importaciones"


def dir_exportaciones() -> Path:
    return dir_datos() / "exportaciones"


def dir_informes() -> Path:
    return Path(os.environ.get("FINANZAS_INFORMES", RAIZ / "informes"))
