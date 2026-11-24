"""Fechas y periodos mensuales."""
from __future__ import annotations

import calendar
import os
from dataclasses import dataclass
from datetime import date, timedelta

from .formato import MESES


def hoy() -> date:
    """Fecha actual. FINANZAS_HOY permite fijarla en pruebas (AAAA-MM-DD)."""
    fijada = os.environ.get("FINANZAS_HOY")
    return date.fromisoformat(fijada) if fijada else date.today()


@dataclass(frozen=True, order=True)
class Mes:
    anio: int
    mes: int

    @classmethod
    def de(cls, valor: str | date | None) -> "Mes":
        """Acepta 'AAAA-MM', una fecha o None (mes actual)."""
        if valor is None or valor == "":
            f = hoy()
            return cls(f.year, f.month)
        if isinstance(valor, date):
            return cls(valor.year, valor.month)
        anio, mes = valor.split("-")[:2]
        m = cls(int(anio), int(mes))
        if not 1 <= m.mes <= 12:
            raise ValueError(f"Mes inválido: {valor}")
        return m

    @property
    def inicio(self) -> date:
        return date(self.anio, self.mes, 1)

    @property
    def fin(self) -> date:
        return date(self.anio, self.mes, calendar.monthrange(self.anio, self.mes)[1])

    @property
    def dias(self) -> int:
        return self.fin.day

    def anterior(self) -> "Mes":
        return Mes(self.anio - 1, 12) if self.mes == 1 else Mes(self.anio, self.mes - 1)

    def siguiente(self) -> "Mes":
        return Mes(self.anio + 1, 1) if self.mes == 12 else Mes(self.anio, self.mes + 1)

    def contiene(self, f: date) -> bool:
        return self.inicio <= f <= self.fin

    @property
    def clave(self) -> str:
        return f"{self.anio:04d}-{self.mes:02d}"

    @property
    def nombre(self) -> str:
        return MESES[self.mes - 1]

    @property
    def etiqueta(self) -> str:
        return f"{MESES[self.mes - 1].capitalize()} {self.anio}"

    @property
    def etiqueta_corta(self) -> str:
        return f"{MESES[self.mes - 1][:3]} {str(self.anio)[2:]}"

    def __str__(self) -> str:
        return self.clave


def dia_equivalente(mes: Mes, dia: int) -> date:
    """El mismo día en otro mes, ajustado si ese mes es más corto (31 -> 30)."""
    return date(mes.anio, mes.mes, min(dia, mes.dias))


def rango_meses(hasta: Mes, cantidad: int) -> list[Mes]:
    meses = [hasta]
    for _ in range(cantidad - 1):
        meses.append(meses[-1].anterior())
    return list(reversed(meses))


def dias_entre(a: date, b: date) -> int:
    return abs((b - a).days)


UN_DIA = timedelta(days=1)
