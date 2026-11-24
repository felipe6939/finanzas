"""Lectura de archivos de entrada a una lista de líneas de texto (todo local)."""
from __future__ import annotations

from pathlib import Path

EXTENSIONES = (".docx", ".txt", ".md")


def _lineas_texto(texto: str) -> list[str]:
    # \v y \x0b son saltos de línea suaves de Word;   el de algunos teléfonos.
    for sep in ("\r\n", "\r", "\v", " ", " "):
        texto = texto.replace(sep, "\n")
    return [l.rstrip() for l in texto.split("\n")]


def leer_docx(ruta: Path) -> list[str]:
    from docx import Document

    documento = Document(str(ruta))
    lineas: list[str] = []
    for bloque in documento.element.body.iterchildren():
        etiqueta = bloque.tag.rsplit("}", 1)[-1]
        if etiqueta == "p":
            texto = "".join(t.text or "" for t in bloque.iter() if t.tag.endswith("}t"))
            # Saltos de línea dentro del mismo párrafo (<w:br/>).
            partes = []
            actual = ""
            for nodo in bloque.iter():
                fin = nodo.tag.rsplit("}", 1)[-1]
                if fin == "t":
                    actual += nodo.text or ""
                elif fin in ("br", "cr"):
                    partes.append(actual)
                    actual = ""
            partes.append(actual)
            lineas.extend(_lineas_texto("\n".join(partes)) if texto else [""])
        elif etiqueta == "tbl":
            for fila in bloque.iter():
                if fila.tag.endswith("}tr"):
                    celdas = []
                    for celda in fila.iter():
                        if celda.tag.endswith("}tc"):
                            celdas.append("".join(t.text or "" for t in celda.iter() if t.tag.endswith("}t")))
                    lineas.append(" ".join(c.strip() for c in celdas if c.strip()))
    return lineas


def leer_txt(ruta: Path) -> list[str]:
    datos = ruta.read_bytes()
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            return _lineas_texto(datos.decode(codificacion))
        except UnicodeDecodeError:
            continue
    raise ValueError("No se pudo leer el texto del archivo (codificación desconocida).")


def leer_archivo(ruta: Path) -> list[str]:
    ruta = Path(ruta)
    extension = ruta.suffix.lower()
    if extension == ".docx":
        return leer_docx(ruta)
    if extension in (".txt", ".md"):
        return leer_txt(ruta)
    raise ValueError(
        f"Formato no soportado: {extension or 'sin extensión'}. Usa .docx o .txt (o pega el texto)."
    )


def leer_texto(texto: str) -> list[str]:
    return _lineas_texto(texto)
