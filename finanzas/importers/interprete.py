"""Intérprete local del formato de registro diario.

Formato (tolerante, pensado para escribirse en el celular):

    16/11
    45.000 almuerzo
    12k bus
    gasolina 80.000 bancolombia
    +3.200.000 salario bbva
    ahorro 300.000 viaje
    25.500 farmacia #salud (para mamá)

Reglas:
- Una línea con solo una fecha fija la fecha de las líneas siguientes.
  Una fecha al inicio de una línea aplica solo a esa línea.
- Sin signo = gasto · '+' o 'ingreso' = ingreso · 'ahorro' = ahorro.
- El valor admite 45.000 · 45000 · 45k · 45 mil · 20 lucas · 1,2 millones · 2 palos.
- La cuenta es una palabra: nequi, bancolombia, bbva, efectivo (o sus alias).
- #etiqueta fuerza la categoría; (texto) es una observación.

El intérprete NUNCA inventa datos: si no encuentra fecha, valor o categoría, el
campo queda vacío y la línea se marca para revisión. Es una función pura: recibe
el contexto (cuentas, categorías, reglas, metas) y no toca la base de datos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..core.texto import capitalizar, contiene_frase, normalizar

MESES_TEXTO = {
    "ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3, "abr": 4, "abril": 4,
    "may": 5, "mayo": 5, "jun": 6, "junio": 6, "jul": 7, "julio": 7, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9, "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11, "dic": 12, "diciembre": 12,
}
DIAS = r"(?:lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo|lun|mar|mie|mié|jue|vie|sab|sáb|dom)"

_FECHA_NUM = r"(\d{1,2})\s*[/-]\s*(\d{1,2})(?:\s*[/-]\s*(\d{2,4}))?"
_FECHA_TXT = r"(\d{1,2})\s*(?:de\s+)?([a-záéíóú]{3,10})\.?(?:\s*(?:de|del)?\s*(\d{4}))?"
RE_LINEA_FECHA = re.compile(
    rf"^\s*(?:{DIAS}[\s,]*)?(?:{_FECHA_NUM}|{_FECHA_TXT})\s*:?\s*$", re.IGNORECASE
)
RE_FECHA_INICIAL = re.compile(rf"^\s*(?:{DIAS}[\s,]*)?{_FECHA_NUM}\s*[:\-–]?\s+(.+)$", re.IGNORECASE)

MULTIPLICADORES = {
    "k": 1_000, "mil": 1_000, "luca": 1_000, "lucas": 1_000,
    "m": 1_000_000, "mm": 1_000_000, "millon": 1_000_000, "millones": 1_000_000,
    "millón": 1_000_000, "palo": 1_000_000, "palos": 1_000_000,
}
RE_MILES = re.compile(r"^\$?(\d{1,3}(?:[.,]\d{3})+)$")
RE_ENTERO = re.compile(r"^\$?(\d+)$")
RE_CON_SUFIJO = re.compile(
    r"^\$?(\d+(?:[.,]\d{1,3})?)(k|mil|lucas?|m|mm|millon(?:es)?|millón|palos?)$", re.IGNORECASE
)
RE_DECIMAL = re.compile(r"^\$?(\d+[.,]\d{1,3})$")
RE_NOTA = re.compile(r"\(([^)]*)\)")
# #categoría o #padre/hija (para distinguir subcategorías con el mismo nombre).
RE_ETIQUETA = re.compile(r"#([\wáéíóúñÁÉÍÓÚÑ]+(?:/[\wáéíóúñÁÉÍÓÚÑ]+)?)")
PALABRAS_INGRESO = {"ingreso", "ingresó", "recibí", "recibi"}
PALABRAS_AHORRO = {"ahorro", "ahorré", "ahorre", "ahorrar"}
CONECTORES_CUENTA = {"en", "por", "con", "desde", "de", "a", "x"}


@dataclass
class CuentaCtx:
    id: int
    nombre: str
    alias: list[str]


@dataclass
class CategoriaCtx:
    id: int
    nombre: str
    nombre_completo: str
    tipo: str
    es_principal: bool
    padre: str | None = None


@dataclass
class ReglaCtx:
    patron: str
    tipo: str
    categoria_id: int


@dataclass
class MetaCtx:
    id: int
    nombre: str


@dataclass
class Contexto:
    cuentas: list[CuentaCtx]
    cuenta_defecto_id: int | None
    categorias: list[CategoriaCtx]
    reglas: list[ReglaCtx]
    metas: list[MetaCtx]
    hoy: date


@dataclass
class Interpretacion:
    num_linea: int
    texto: str
    clase: str  # 'vacia' | 'fecha' | 'movimiento' | 'sin_valor'
    fecha_contexto: date | None = None
    tipo: str | None = None
    fecha: date | None = None
    valor: int | None = None
    descripcion: str = ""
    cuenta_id: int | None = None
    categoria_id: int | None = None
    meta_id: int | None = None
    observaciones: str = ""
    origen: dict = field(default_factory=dict)  # cómo se obtuvo cada campo
    notas: list[str] = field(default_factory=list)  # pistas para la revisión


# --------------------------------------------------------------------- fechas

def _anio_probable(dia: int, mes: int, hoy: date) -> int:
    """Sin año explícito: el año que deja la fecha más cerca de hoy (pasada o futura).

    17 nov 2026: «20 nov» → 2026 · «2 ene» → 2027 · «28 dic» → 2026.
    """
    candidatas = []
    for a in (hoy.year - 1, hoy.year, hoy.year + 1):
        try:
            candidatas.append((abs((date(a, mes, dia) - hoy).days), a))
        except ValueError:
            continue
    return min(candidatas)[1] if candidatas else hoy.year


def _construir_fecha(dia: str, mes: str, anio: str | None, hoy: date) -> tuple[date | None, str | None]:
    d = int(dia)
    m = int(mes) if mes.isdigit() else MESES_TEXTO.get(normalizar(mes))
    if m is None:
        return None, None
    nota = None
    if anio:
        a = int(anio)
        if a < 100:
            a += 2000
    else:
        a = _anio_probable(d, m, hoy)
        if a != hoy.year:
            nota = f"Año deducido: {a} (la fecha más cercana a hoy)."
    try:
        return date(a, m, d), nota
    except ValueError:
        return None, None


def interpretar_linea_fecha(texto: str, hoy: date) -> tuple[date | None, str | None] | None:
    """Si la línea es solo una fecha, la devuelve. None si no es una línea de fecha."""
    m = RE_LINEA_FECHA.match(texto)
    if not m:
        return None
    if m.group(1):
        return _construir_fecha(m.group(1), m.group(2), m.group(3), hoy)
    resultado = _construir_fecha(m.group(4), m.group(5), m.group(6), hoy)
    return resultado if resultado[0] else None


# --------------------------------------------------------------------- valores

def _valor_token(token: str, siguiente: str | None) -> tuple[int | None, str, bool]:
    """Devuelve (valor, fuerza, consume_siguiente).

    fuerza: 'fuerte' (formato de dinero claro) o 'debil' (número pequeño sin formato).
    """
    t = token.lower().rstrip(",;:.$")
    if m := RE_CON_SUFIJO.match(t):
        numero = float(m.group(1).replace(",", "."))
        return round(numero * MULTIPLICADORES[m.group(2).lower()]), "fuerte", False
    if siguiente and (sig := siguiente.lower().rstrip(",;:.")) in MULTIPLICADORES and sig not in ("m",):
        if m := (RE_ENTERO.match(t) or RE_DECIMAL.match(t)):
            numero = float(m.group(1).replace(",", "."))
            return round(numero * MULTIPLICADORES[sig]), "fuerte", True
    if m := RE_MILES.match(t):
        return int(re.sub(r"[.,]", "", m.group(1))), "fuerte", False
    if m := RE_ENTERO.match(t):
        n = int(m.group(1))
        if t.startswith("$") or n >= 1000:
            return n, "fuerte", False
        return n, "debil", False
    return None, "", False


def _es_anio_de_mes(tokens: list[str], i: int) -> bool:
    t = tokens[i].rstrip(",;:.")
    if not (len(t) == 4 and t.isdigit() and 2000 <= int(t) <= 2099):
        return False
    previos = [normalizar(x) for x in tokens[max(0, i - 2):i]]
    return any(p in MESES_TEXTO for p in previos)


# ------------------------------------------------------------------ categorías

def clave_etiqueta(nombre: str) -> str:
    """'Servicios públicos' -> 'serviciospublicos'; '4x1000 (GMF)' -> '4x1000gmf'."""
    return normalizar(nombre).replace("#", "").replace(" ", "")


def etiquetas_categorias(categorias: list[CategoriaCtx]) -> list[dict]:
    """La etiqueta que identifica sin ambigüedad a cada categoría (para el autocompletado).

    Si dos categorías del mismo tipo tienen el mismo nombre, la subcategoría usa
    #padre/hija y la principal #nombre/general.
    """
    res = []
    for c in categorias:
        clave = clave_etiqueta(c.nombre)
        repetida = sum(1 for o in categorias if o.tipo == c.tipo and clave_etiqueta(o.nombre) == clave) > 1
        if not repetida:
            etiqueta = clave
        elif c.padre:
            etiqueta = f"{clave_etiqueta(c.padre)}/{clave}"
        else:
            etiqueta = f"{clave}/general"
        res.append({"id": c.id, "nombre": c.nombre, "padre": c.padre, "tipo": c.tipo, "etiqueta": etiqueta})
    return res


def _por_etiqueta(etiqueta: str, tipo: str, ctx: Contexto) -> list[CategoriaCtx]:
    e = normalizar(etiqueta)
    candidatas = [c for c in ctx.categorias if c.tipo == tipo]
    if "/" in etiqueta:
        padre, hija = (clave_etiqueta(x) for x in etiqueta.split("/", 1))
        if hija == "general":
            return [c for c in candidatas if c.es_principal and clave_etiqueta(c.nombre) == padre]
        return [c for c in candidatas if not c.es_principal and clave_etiqueta(c.nombre) == hija
                and clave_etiqueta(c.padre or "") == padre]
    exactas = [c for c in candidatas if clave_etiqueta(c.nombre) == clave_etiqueta(etiqueta)]
    if len(exactas) > 1:
        # 'Salario' (principal) y 'Salario › Salario': gana la subcategoría, la más específica.
        hijas = [c for c in exactas if not c.es_principal]
        if len(hijas) == 1 and all(c.es_principal for c in exactas if c is not hijas[0]):
            return hijas
    if exactas:
        return exactas
    reglas = {r.categoria_id for r in ctx.reglas if r.tipo == tipo and r.patron == e}
    if reglas:
        return [c for c in candidatas if c.id in reglas]
    if len(e) >= 4:
        return [c for c in candidatas if normalizar(c.nombre).startswith(e)
                or e in normalizar(c.nombre).split()]
    return []


def clasificar(descripcion: str, tipo: str, ctx: Contexto) -> tuple[int | None, list[int]]:
    """Aplica las reglas. Devuelve (categoría elegida, candidatas en conflicto)."""
    texto = normalizar(descripcion)
    def coincide(patron: str) -> bool:  # admite plural: 'almuerzo' -> 'almuerzos'
        return any(contiene_frase(texto, patron + s) for s in ("", "s", "es"))

    coincidencias = [r for r in ctx.reglas if r.tipo == tipo and coincide(r.patron)]
    if not coincidencias:
        return None, []
    categorias = {r.categoria_id for r in coincidencias}
    if len(categorias) == 1:
        return coincidencias[0].categoria_id, []
    # Gana la frase más específica (más larga) si es única.
    maximo = max(len(r.patron) for r in coincidencias)
    mas_largas = {r.categoria_id for r in coincidencias if len(r.patron) == maximo}
    if len(mas_largas) == 1:
        return mas_largas.pop(), []
    return None, sorted(categorias)


# ---------------------------------------------------------------- interpretar

def interpretar_movimiento(num: int, texto: str, fecha_ctx: date | None, ctx: Contexto,
                           nota_fecha: str | None = None) -> Interpretacion:
    r = Interpretacion(num_linea=num, texto=texto, clase="movimiento", fecha_contexto=fecha_ctx)
    cuerpo = texto.strip().lstrip("•·").strip()

    # Fecha al inicio de la línea (solo para esta línea).
    fecha = fecha_ctx
    if m := RE_FECHA_INICIAL.match(cuerpo):
        f, nota = _construir_fecha(m.group(1), m.group(2), m.group(3), ctx.hoy)
        if f:
            fecha, nota_fecha = f, nota
            cuerpo = m.group(4)
            r.origen["fecha"] = "linea"
    if fecha and "fecha" not in r.origen:
        r.origen["fecha"] = "encabezado"
    r.fecha = fecha
    if nota_fecha:
        r.notas.append(nota_fecha)

    # Observaciones entre paréntesis y etiquetas de categoría.
    r.observaciones = " · ".join(n.strip() for n in RE_NOTA.findall(cuerpo) if n.strip())
    cuerpo = RE_NOTA.sub(" ", cuerpo)
    etiquetas = RE_ETIQUETA.findall(cuerpo)
    cuerpo = RE_ETIQUETA.sub(" ", cuerpo)

    # Tipo de movimiento.
    tipo = "gasto"
    c = cuerpo.strip()
    if c.startswith("+"):
        tipo, c = "ingreso", c[1:]
    elif c.startswith("-") or c.startswith("–"):
        c = c[1:]
    tokens = c.split()
    if tokens and normalizar(tokens[0]) in {normalizar(p) for p in PALABRAS_INGRESO}:
        tipo, tokens = "ingreso", tokens[1:]
    elif tokens and normalizar(tokens[0]) in {normalizar(p) for p in PALABRAS_AHORRO}:
        tipo, tokens = "ahorro", tokens[1:]
    r.tipo = tipo

    # Valor: se distinguen candidatos fuertes (formato de dinero) y débiles.
    fuertes, debiles, restantes = [], [], []
    originales: dict[int, str] = {}  # posición -> texto original del número
    i = 0
    while i < len(tokens):
        siguiente = tokens[i + 1] if i + 1 < len(tokens) else None
        if _es_anio_de_mes(tokens, i):  # 'noviembre 2026' es un año, no un valor
            restantes.append(tokens[i])
            i += 1
            continue
        valor, fuerza, consume = _valor_token(tokens[i], siguiente)
        if valor is not None and valor > 0:
            (fuertes if fuerza == "fuerte" else debiles).append((valor, len(restantes)))
            texto_original = " ".join(tokens[i:i + (2 if consume else 1)])
            originales[len(restantes)] = texto_original
            restantes.append(None)  # hueco donde estaba el número
            i += 2 if consume else 1
            continue
        restantes.append(tokens[i])
        i += 1

    elegido = None
    if len(fuertes) == 1:
        elegido = fuertes[0]
    elif len(fuertes) > 1:
        r.notas.append("Hay varios valores posibles: " + ", ".join(f"$ {v:,}".replace(",", ".")
                                                                   for v, _ in fuertes) + ".")
    elif len(debiles) == 1:
        elegido = debiles[0]
        r.notas.append(f"El valor $ {elegido[0]} es inusualmente bajo: ¿faltan ceros o una 'k'?")
    elif len(debiles) > 1:
        r.notas.append("No está claro cuál número es el valor.")

    if elegido:
        r.valor = elegido[0]
        r.origen["valor"] = "texto"
    # Los números que no son el valor vuelven a la descripción (p. ej. '2 almuerzos').
    posicion_elegida = elegido[1] if elegido else None
    palabras = [originales[i] if t is None else t
                for i, t in enumerate(restantes) if i != posicion_elegida]

    # Cuenta.
    encontradas, sin_cuenta = [], []
    alias = {a: cu.id for cu in ctx.cuentas for a in cu.alias}
    for p in palabras:
        if (cid := alias.get(normalizar(p))) is not None:
            encontradas.append(cid)
            if sin_cuenta and normalizar(sin_cuenta[-1]) in CONECTORES_CUENTA:
                sin_cuenta.pop()
        else:
            sin_cuenta.append(p)
    distintas = sorted(set(encontradas))
    if len(distintas) == 1:
        r.cuenta_id, r.origen["cuenta"] = distintas[0], "texto"
    elif len(distintas) > 1:
        nombres = [cu.nombre for cu in ctx.cuentas if cu.id in distintas]
        r.notas.append(f"Se mencionan varias cuentas: {', '.join(nombres)}.")
    else:
        r.cuenta_id, r.origen["cuenta"] = ctx.cuenta_defecto_id, "defecto"

    r.descripcion = capitalizar(" ".join(sin_cuenta).strip(" -–:,;"))

    # Categoría (gasto/ingreso) o meta (ahorro).
    if tipo in ("gasto", "ingreso"):
        if etiquetas:
            candidatas = {c.id: c for e in etiquetas for c in _por_etiqueta(e, tipo, ctx)}
            if len(candidatas) == 1:
                r.categoria_id, r.origen["categoria"] = next(iter(candidatas)), "etiqueta"
            elif candidatas:
                r.notas.append("La etiqueta coincide con varias categorías: "
                               + ", ".join(c.nombre_completo for c in candidatas.values()) + ".")
            else:
                r.notas.append(f"No existe una categoría de {tipo} llamada '#{etiquetas[0]}'.")
        # Con una etiqueta explícita que no se pudo resolver, no se adivina con reglas.
        if r.categoria_id is None and not etiquetas:
            cat, conflicto = clasificar(r.descripcion, tipo, ctx)
            if cat:
                r.categoria_id, r.origen["categoria"] = cat, "regla"
            elif conflicto:
                nombres = [c.nombre_completo for c in ctx.categorias if c.id in conflicto]
                r.notas.append("La descripción encaja en varias categorías: " + ", ".join(nombres) + ".")
        if not r.descripcion and r.categoria_id and r.origen.get("categoria") == "etiqueta":
            r.descripcion = next(c.nombre for c in ctx.categorias if c.id == r.categoria_id)
            r.origen["descripcion"] = "etiqueta"
    else:
        texto_n = normalizar(r.descripcion)
        metas = [m for m in ctx.metas if contiene_frase(texto_n, normalizar(m.nombre))]
        if len(metas) == 1:
            r.meta_id, r.origen["meta"] = metas[0].id, "texto"
        if not r.descripcion:
            r.descripcion = "Ahorro"
            r.origen["descripcion"] = "tipo"

    if r.valor is None and not fuertes and not debiles:
        r.clase = "sin_valor"
    return r


def interpretar(lineas: list[str], ctx: Contexto) -> list[Interpretacion]:
    """Interpreta un documento completo, línea por línea, conservando el número de línea."""
    resultado: list[Interpretacion] = []
    fecha_ctx: date | None = None
    nota_ctx: str | None = None
    for num, texto in enumerate(lineas, start=1):
        if not texto.strip():
            resultado.append(Interpretacion(num, texto, "vacia", fecha_ctx))
            continue
        como_fecha = interpretar_linea_fecha(texto, ctx.hoy)
        if como_fecha is not None:
            fecha, nota = como_fecha
            if fecha:
                fecha_ctx, nota_ctx = fecha, nota
                resultado.append(Interpretacion(num, texto, "fecha", fecha_ctx, fecha=fecha))
                continue
        resultado.append(interpretar_movimiento(num, texto, fecha_ctx, ctx, nota_ctx))
    return resultado
