"""Respaldos, restauración y exportación.

Se usa la API oficial de copia de SQLite (no una copia de archivo), que produce
una copia consistente incluso con la base abierta. Cada respaldo se verifica con
PRAGMA integrity_check antes de darlo por bueno.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config
from .core.fechas import hoy

CONSERVAR_RECIENTES = 30  # además se conserva el último respaldo de cada mes


def _verificar(ruta: Path) -> None:
    con = sqlite3.connect(ruta)
    try:
        resultado = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    if resultado != "ok":
        raise RuntimeError(f"El respaldo {ruta.name} no pasó la verificación: {resultado}")


def crear_respaldo(con: sqlite3.Connection, motivo: str = "manual") -> Path:
    destino_dir = config.dir_respaldos()
    destino_dir.mkdir(parents=True, exist_ok=True)
    motivo = re.sub(r"[^a-z0-9-]+", "-", motivo.lower()).strip("-") or "manual"
    marca = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    destino = destino_dir / f"finanzas_{marca}_{motivo}.db"
    copia = sqlite3.connect(destino)
    try:
        con.backup(copia)
    finally:
        copia.close()
    _verificar(destino)
    rotar_respaldos()
    return destino


def listar_respaldos() -> list[Path]:
    carpeta = config.dir_respaldos()
    if not carpeta.exists():
        return []
    return sorted(carpeta.glob("finanzas_*.db"), reverse=True)


def rotar_respaldos() -> list[Path]:
    """Conserva los 30 más recientes y el último de cada mes. Devuelve los borrados."""
    todos = listar_respaldos()  # del más reciente al más antiguo
    conservar = set(todos[:CONSERVAR_RECIENTES])
    vistos_mes: set[str] = set()
    for ruta in todos:
        mes = ruta.name.split("_")[1][:6]  # AAAAMM
        if mes not in vistos_mes:
            vistos_mes.add(mes)
            conservar.add(ruta)
    borrados = [r for r in todos if r not in conservar]
    for ruta in borrados:
        ruta.unlink()
    return borrados


def respaldo_diario(con: sqlite3.Connection) -> Path | None:
    """Crea un respaldo al abrir la app si hoy todavía no hay ninguno."""
    prefijo = f"finanzas_{hoy():%Y%m%d}"
    if any(r.name.startswith(prefijo) for r in listar_respaldos()):
        return None
    return crear_respaldo(con, motivo="diario")


def restaurar(ruta_respaldo: Path, ruta_db: Path | None = None) -> Path:
    """Reemplaza la base actual por un respaldo. Antes respalda la actual.

    Devuelve la ruta del respaldo de seguridad de la base reemplazada.
    """
    ruta_respaldo = Path(ruta_respaldo)
    ruta_db = Path(ruta_db or config.ruta_db())
    if not ruta_respaldo.exists():
        raise FileNotFoundError(ruta_respaldo)
    _verificar(ruta_respaldo)
    actual = sqlite3.connect(ruta_db)
    try:
        seguridad = crear_respaldo(actual, motivo="antes-de-restaurar")
        origen = sqlite3.connect(ruta_respaldo)
        try:
            origen.backup(actual)
        finally:
            origen.close()
    finally:
        actual.close()
    _verificar(ruta_db)
    return seguridad


CONSULTA_EXPORTAR = """
SELECT m.id, m.fecha, m.tipo, m.descripcion, m.valor,
       COALESCE(cp.nombre, c.nombre) AS categoria,
       CASE WHEN cp.id IS NOT NULL THEN c.nombre ELSE '' END AS subcategoria,
       m.naturaleza, m.necesidad, cu.nombre AS cuenta, ma.nombre AS meta,
       m.observaciones, m.origen, m.lote_id, m.creado_en, m.actualizado_en
FROM movimientos m
JOIN cuentas cu ON cu.id = m.cuenta_id
LEFT JOIN categorias c ON c.id = m.categoria_id
LEFT JOIN categorias cp ON cp.id = c.padre_id
LEFT JOIN metas_ahorro ma ON ma.id = m.meta_id
WHERE m.eliminado_en IS NULL
ORDER BY m.fecha, m.id
"""


def exportar(con: sqlite3.Connection, formato: str = "xlsx") -> Path:
    carpeta = config.dir_exportaciones()
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    filas = con.execute(CONSULTA_EXPORTAR).fetchall()
    columnas = [d[0] for d in con.execute(CONSULTA_EXPORTAR).description]

    if formato == "csv":
        destino = carpeta / f"movimientos_{marca}.csv"
        with destino.open("w", newline="", encoding="utf-8-sig") as f:
            escritor = csv.writer(f, delimiter=";")
            escritor.writerow(columnas)
            escritor.writerows([tuple(r) for r in filas])
        return destino

    from openpyxl import Workbook
    from openpyxl.styles import Font

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Movimientos"
    hoja.append(columnas)
    for fila in filas:
        hoja.append(tuple(fila))
    for celda in hoja[1]:
        celda.font = Font(bold=True)
    col_valor = columnas.index("valor") + 1
    for (celda,) in hoja.iter_rows(min_row=2, min_col=col_valor, max_col=col_valor):
        celda.number_format = '"$" #,##0'
    hoja.freeze_panes = "A2"

    for nombre, sql in (
        ("Cuentas", "SELECT nombre, tipo, saldo_inicial, alias, activa FROM cuentas ORDER BY orden"),
        ("Categorías", "SELECT c.id, c.tipo, COALESCE(p.nombre, '') AS categoria_padre, c.nombre, "
                       "c.naturaleza, c.necesidad, c.activa FROM categorias c "
                       "LEFT JOIN categorias p ON p.id = c.padre_id ORDER BY c.tipo, c.id"),
        ("Metas", "SELECT nombre, monto_objetivo, fecha_objetivo, activa FROM metas_ahorro"),
        ("Importaciones", "SELECT id, archivo_nombre, estado, creado_en, confirmado_en, revertido_en "
                          "FROM lotes_importacion ORDER BY id"),
        ("Me deben", "SELECT p.id, p.persona, p.valor, COALESCE(p.fecha, 'inicial') AS fecha, "
                     "c.nombre AS cuenta, p.nota, p.eliminado_en FROM prestamos p "
                     "LEFT JOIN cuentas c ON c.id = p.cuenta_id ORDER BY p.id"),
        ("Pagos recibidos", "SELECT a.id, a.prestamo_id, p.persona, a.fecha, a.valor, c.nombre AS cuenta, "
                            "a.nota, a.eliminado_en FROM abonos_prestamo a "
                            "JOIN prestamos p ON p.id = a.prestamo_id "
                            "JOIN cuentas c ON c.id = a.cuenta_id ORDER BY a.fecha, a.id"),
        ("Entre cuentas", "SELECT t.id, t.fecha, t.valor, o.nombre AS desde, d.nombre AS hacia, t.nota, "
                          "t.eliminado_en FROM transferencias t JOIN cuentas o ON o.id = t.cuenta_origen_id "
                          "JOIN cuentas d ON d.id = t.cuenta_destino_id ORDER BY t.fecha, t.id"),
        ("Configuración", "SELECT clave, valor FROM configuracion"),
    ):
        cursor = con.execute(sql)
        h = libro.create_sheet(nombre)
        h.append([d[0] for d in cursor.description])
        for fila in cursor.fetchall():
            h.append(tuple(fila))
        for celda in h[1]:
            celda.font = Font(bold=True)

    destino = carpeta / f"finanzas_{marca}.xlsx"
    libro.save(destino)
    return destino
