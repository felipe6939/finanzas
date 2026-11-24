"""Normalización de texto para comparar descripciones y reglas."""
from __future__ import annotations

import re
import unicodedata

_ESPACIOS = re.compile(r"\s+")
_NO_PALABRA = re.compile(r"[^a-z0-9#\s]")


def sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def normalizar(texto: str | None) -> str:
    """'  Almuerzo   Corrientazo!! ' -> 'almuerzo corrientazo'."""
    if not texto:
        return ""
    t = sin_tildes(texto).lower()
    t = _NO_PALABRA.sub(" ", t)
    return _ESPACIOS.sub(" ", t).strip()


def contiene_frase(texto_normalizado: str, frase_normalizada: str) -> bool:
    """Coincidencia por palabras completas: 'bus' no coincide con 'autobusero'."""
    if not frase_normalizada:
        return False
    return f" {frase_normalizada} " in f" {texto_normalizado} "


def capitalizar(texto: str) -> str:
    texto = texto.strip()
    return texto[:1].upper() + texto[1:] if texto else texto
