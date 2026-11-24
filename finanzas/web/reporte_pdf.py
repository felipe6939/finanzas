"""Reporte PDF: se arma como HTML (gráficos en SVG generados aquí) y Edge/Chrome lo imprime a PDF.

Todo ocurre en el equipo: el HTML se escribe en una carpeta temporal, el navegador lo abre
desde el disco en modo headless (sin red, perfil temporal) y el PDF se devuelve y se borra.
No se guarda copia en ningún lado.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

from ..core.formato import cop, cop_corto

# Paleta de las series (la misma del dashboard, modo claro): ingresos, gastos, ahorro.
C_INGRESO, C_GASTO, C_AHORRO = "#2a78d6", "#eb6834", "#1baf7a"
TINTA, TINTA_2, TINTA_3, REJILLA = "#0b0b0b", "#52514e", "#898781", "#e6e5e0"


class SinNavegador(RuntimeError):
    pass


# ----------------------------------------------------------------------------- gráficos

def _escala(maximo: int) -> tuple[int, int]:
    """Tope 'redondo' del eje y su paso (4 divisiones)."""
    if maximo <= 0:
        return 100_000, 25_000
    import math
    paso_bruto = maximo / 4
    potencia = 10 ** math.floor(math.log10(paso_bruto))
    for m in (1, 2, 2.5, 5, 10):
        if paso_bruto <= m * potencia:
            paso = int(m * potencia)
            break
    return paso * 4, paso


def _barra_v(x: float, y: float, w: float, h: float, color: str) -> str:
    """Barra vertical con el extremo de datos redondeado (4 px) y la base recta."""
    if h <= 0:
        return ""
    r = min(4, w / 2, h)
    return (f'<path d="M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
            f'H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z" fill="{color}"/>')


def _barra_h(x: float, y: float, w: float, h: float, color: str) -> str:
    if w <= 0:
        return ""
    r = min(4, h / 2, w)
    return (f'<path d="M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} '
            f'V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z" fill="{color}"/>')


def svg_mensual(meses: list[dict], ancho: int = 680, alto: int = 230) -> str:
    """Barras agrupadas por mes: ingresos, gastos y ahorro."""
    series = (("ingresos", "Ingresos", C_INGRESO), ("gastos", "Gastos", C_GASTO), ("ahorro", "Ahorro", C_AHORRO))
    tope, paso = _escala(max((m[k] for m in meses for k, _, _ in series), default=0))
    izq, der, arr, abj = 62, 8, 30, 24
    ah, aw = alto - arr - abj, ancho - izq - der
    partes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" width="100%" '
              f'role="img" aria-label="Ingresos, gastos y ahorro por mes">']
    lx = izq
    for _, nombre, color in series:  # leyenda
        partes.append(f'<rect x="{lx}" y="4" width="10" height="10" rx="2" fill="{color}"/>'
                      f'<text x="{lx + 15}" y="13" font-size="11" fill="{TINTA_2}">{nombre}</text>')
        lx += 15 + len(nombre) * 6.5 + 18
    for i in range(5):  # rejilla
        v = paso * i
        y = arr + ah - ah * v / tope
        partes.append(f'<line x1="{izq}" x2="{ancho - der}" y1="{y:.1f}" y2="{y:.1f}" stroke="{REJILLA}" '
                      f'stroke-width="1"/><text x="{izq - 6}" y="{y + 3.5:.1f}" font-size="10" fill="{TINTA_3}" '
                      f'text-anchor="end">{escape(cop_corto(v))}</text>')
    grupo = aw / max(len(meses), 1)
    bw = min(22, (grupo * 0.78) / 3 - 2)
    for i, m in enumerate(meses):
        x0 = izq + grupo * i + (grupo - (bw * 3 + 4)) / 2
        for j, (clave, _, color) in enumerate(series):
            h = ah * m[clave] / tope
            partes.append(_barra_v(x0 + j * (bw + 2), arr + ah - h, bw, h, color))
        etiqueta = m["etiqueta"] + ("*" if m["parcial"] else "")
        partes.append(f'<text x="{izq + grupo * i + grupo / 2:.1f}" y="{alto - 6}" font-size="10.5" '
                      f'fill="{TINTA_2}" text-anchor="middle">{escape(etiqueta)}</text>')
    partes.append(f'<line x1="{izq}" x2="{ancho - der}" y1="{arr + ah}" y2="{arr + ah}" stroke="{TINTA_3}" stroke-width="1"/>')
    partes.append("</svg>")
    return "".join(partes)


def svg_categorias(items: list[dict], color: str, ancho: int = 680, maximo_filas: int = 8,
                   etiqueta: str = "Categorías") -> str:
    """Barras horizontales por categoría; a partir de la fila `maximo_filas` se agrupan en «Otras»."""
    filas = list(items)
    if len(filas) > maximo_filas:
        resto = filas[maximo_filas - 1:]
        filas = filas[:maximo_filas - 1] + [{"nombre": f"Otras ({len(resto)})",
                                             "total": sum(f["total"] for f in resto),
                                             "participacion": sum(f["participacion"] for f in resto)}]
    alto_fila, izq, der = 24, 170, 150
    alto = alto_fila * len(filas) + 4
    tope = max((f["total"] for f in filas), default=0) or 1
    partes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" width="100%" '
              f'role="img" aria-label="{escape(etiqueta)}">']
    for i, f in enumerate(filas):
        y = i * alto_fila + 2
        w = (ancho - izq - der) * f["total"] / tope
        nombre = f["nombre"] if len(f["nombre"]) <= 26 else f["nombre"][:25] + "…"
        partes.append(f'<text x="{izq - 8}" y="{y + 15}" font-size="11.5" fill="{TINTA}" text-anchor="end">'
                      f'{escape(nombre)}</text>')
        partes.append(_barra_h(izq, y + 4, max(w, 2), 14, color))
        p = f["participacion"]
        pct = "<1 %" if 0 < p < 0.005 else f"{p * 100:.0f} %"
        partes.append(f'<text x="{izq + w + 8:.1f}" y="{y + 15}" font-size="11" fill="{TINTA_2}">'
                      f'{escape(cop(f["total"]))} · {escape(pct)}</text>')
    partes.append("</svg>")
    return "".join(partes)


def svg_reparto(items: list[dict], colores: dict[str, str], ancho: int = 680) -> str:
    """Una barra 100 % segmentada (p. ej. fijo / variable / extraordinario), con 2 px entre segmentos."""
    total = sum(i["total"] for i in items) or 1
    partes = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} 22" width="100%" role="img" '
              f'aria-label="Reparto">']
    x = 0.0
    utiles = [i for i in items if i["total"] > 0]
    disponible = ancho - 2 * (len(utiles) - 1)
    for n, i in enumerate(utiles):
        w = disponible * i["total"] / total
        radio = 4 if n in (0, len(utiles) - 1) else 0
        partes.append(f'<rect x="{x:.1f}" y="2" width="{w:.1f}" height="18" rx="{radio}" '
                      f'fill="{colores.get(i["clave"], "#b5b3ab")}"/>')
        x += w + 2
    partes.append("</svg>")
    return "".join(partes)


COLORES_NATURALEZA = {"fijo": "#2a78d6", "variable": "#eb6834", "extraordinario": "#8a63d2",
                      "sin clasificar": "#b5b3ab"}
COLORES_NECESIDAD = {"esencial": "#2a78d6", "discrecional": "#eb6834", "sin clasificar": "#b5b3ab"}


def graficos(d: dict) -> dict:
    return {
        "mensual": svg_mensual(d["meses"]) if d["meses"] else "",
        "gastos_mes": svg_categorias(d["gastos_mes"], C_GASTO, etiqueta="Gastos del mes por categoría")
                      if d["gastos_mes"] else "",
        "gastos_total": svg_categorias(d["gastos_total"], C_GASTO, etiqueta="Gastos desde el inicio")
                        if d["gastos_total"] else "",
        "ingresos_total": svg_categorias(d["ingresos_total"], C_INGRESO, etiqueta="Ingresos desde el inicio")
                          if d["ingresos_total"] else "",
        "naturaleza": svg_reparto(d["naturaleza"], COLORES_NATURALEZA) if d["naturaleza"] else "",
        "necesidad": svg_reparto(d["necesidad"], COLORES_NECESIDAD) if d["necesidad"] else "",
    }


# ----------------------------------------------------------------------------- PDF

def navegador() -> str | None:
    """Edge o Chrome instalados (para imprimir el HTML a PDF sin dependencias extra)."""
    propio = os.environ.get("FINANZAS_NAVEGADOR")
    if propio:
        return propio if Path(propio).exists() else None
    candidatos = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidatos += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    for c in candidatos:
        if c.exists():
            return str(c)
    for nombre in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        if ruta := shutil.which(nombre):
            return ruta
    return None


def html_a_pdf(html: str, espera: int = 90) -> bytes:
    exe = navegador()
    if not exe:
        raise SinNavegador("No encontré Microsoft Edge ni Google Chrome para crear el PDF.")
    with tempfile.TemporaryDirectory(prefix="finanzas-reporte-") as tmp:
        tmp_p = Path(tmp)
        entrada, salida = tmp_p / "reporte.html", tmp_p / "reporte.pdf"
        entrada.write_text(html, encoding="utf-8")
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-sync", "--no-pdf-header-footer",
               "--disable-background-networking", "--disable-component-update",
               f"--user-data-dir={tmp_p / 'perfil'}", f"--print-to-pdf={salida}", entrada.as_uri()]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=espera,
                       creationflags=flags, check=False)
        if not salida.exists() or salida.read_bytes()[:4] != b"%PDF":
            raise RuntimeError("El navegador no generó el PDF.")
        return salida.read_bytes()
