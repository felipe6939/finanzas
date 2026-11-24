"""Formato de valores y fechas para Colombia (COP, día-mes-año)."""
from __future__ import annotations

from datetime import date

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def miles(valor: int) -> str:
    """1250000 -> '1.250.000'."""
    return f"{abs(int(valor)):,}".replace(",", ".")


def cop(valor: int | None, signo: bool = False) -> str:
    """1250000 -> '$ 1.250.000'. Con signo=True, los positivos llevan '+'."""
    if valor is None:
        return "—"
    valor = int(valor)
    prefijo = "−" if valor < 0 else ("+" if signo and valor > 0 else "")
    return f"{prefijo}$ {miles(valor)}"


def cop_corto(valor: int | None) -> str:
    """Formato compacto para ejes: 1.250.000 -> '$ 1,3 M'; 45.000 -> '$ 45 mil'."""
    if valor is None:
        return "—"
    v = abs(int(valor))
    signo = "−" if valor < 0 else ""
    if v >= 1_000_000:
        decimas = (v + 50_000) // 100_000  # redondeo hacia arriba en la mitad: 1.250.000 -> 1,3
        texto = f"{decimas // 10},{decimas % 10}".removesuffix(",0") + " M"
    elif v >= 1_000:
        texto = f"{(v + 500) // 1_000} mil"
    else:
        texto = str(v)
    return f"{signo}$ {texto}"


def a_fecha(valor: str | date) -> date:
    return valor if isinstance(valor, date) else date.fromisoformat(valor)


def fecha_corta(valor: str | date | None) -> str:
    """'2026-11-16' -> '16 nov 2026'."""
    if not valor:
        return "—"
    f = a_fecha(valor)
    return f"{f.day} {MESES_CORTOS[f.month - 1]} {f.year}"


def fecha_dia(valor: str | date | None) -> str:
    """'2026-11-16' -> 'lun 16 nov'."""
    if not valor:
        return "—"
    f = a_fecha(valor)
    return f"{DIAS_SEMANA[f.weekday()][:3]} {f.day} {MESES_CORTOS[f.month - 1]}"


def porcentaje(valor: float | None, decimales: int = 0) -> str:
    """0.172 -> '17 %'."""
    if valor is None:
        return "—"
    texto = f"{valor * 100:.{decimales}f}".replace(".", ",")
    return f"{texto} %"
