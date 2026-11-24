"""Flujo de importación por lotes: preparar → revisar/corregir → confirmar (o descartar) → revertir.

Garantías:
- El archivo original se archiva intacto en data/importaciones/ (trazabilidad).
- Cada línea tiene una huella (fecha + texto normalizado + ocurrencia). Reimportar
  el mismo archivo, o el mismo archivo con líneas nuevas al final, solo importa lo nuevo.
  Un índice único en la base de datos lo garantiza aunque fallara la lógica.
- Nada se registra sin confirmación explícita, y la confirmación es todo o nada.
- Antes de confirmar o revertir se crea un respaldo.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from .. import config
from ..core.formato import cop, fecha_corta
from ..core.fechas import hoy
from ..core.texto import normalizar
from ..db.conexion import transaccion
from ..respaldo import crear_respaldo
from ..services import catalogos, movimientos
from ..services.errores import ErrorValidacion
from . import interprete
from .lector import leer_archivo, leer_texto

CAMPOS_EDITABLES = ("tipo", "fecha", "valor", "descripcion", "cuenta_id", "categoria_id", "meta_id",
                    "observaciones")


# ------------------------------------------------------------------- contexto

def categorias_ctx(con: sqlite3.Connection) -> list[interprete.CategoriaCtx]:
    return [interprete.CategoriaCtx(c["id"], c["nombre"], c["nombre_completo"], c["tipo"],
                                    c["padre_id"] is None, c["padre_nombre"])
            for c in catalogos.listar_categorias(con, solo_activas=True)]


def construir_contexto(con: sqlite3.Connection) -> interprete.Contexto:
    defecto = catalogos.cuenta_por_defecto(con)
    return interprete.Contexto(
        cuentas=[interprete.CuentaCtx(c["id"], c["nombre"], [a for a in c["alias"].split(",") if a])
                 for c in catalogos.listar_cuentas(con, solo_activas=True)],
        cuenta_defecto_id=defecto["id"] if defecto else None,
        categorias=categorias_ctx(con),
        reglas=[interprete.ReglaCtx(r["patron"], r["tipo"], r["categoria_id"])
                for r in con.execute("SELECT r.* FROM reglas_clasificacion r JOIN categorias c "
                                     "ON c.id = r.categoria_id WHERE c.activa = 1").fetchall()],
        metas=[interprete.MetaCtx(m["id"], m["nombre"]) for m in catalogos.listar_metas(con, True)],
        hoy=hoy(),
    )


def _huella(fecha: date | None, texto: str, ocurrencia: int) -> str:
    base = f"{fecha.isoformat() if fecha else ''}|{normalizar(texto)}|{ocurrencia}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _archivar(contenido: bytes, nombre: str) -> Path:
    ahora = datetime.now()
    carpeta = config.dir_importaciones() / f"{ahora:%Y}" / f"{ahora:%m}"
    carpeta.mkdir(parents=True, exist_ok=True)
    resumen = hashlib.sha256(contenido).hexdigest()[:8]
    seguro = "".join(c if c.isalnum() or c in "._- " else "_" for c in nombre).strip() or "texto.txt"
    destino = carpeta / f"{ahora:%Y%m%d-%H%M%S}_{resumen}_{seguro}"
    destino.write_bytes(contenido)
    return destino


# ------------------------------------------------------------------- preparar

def preparar_archivo(con: sqlite3.Connection, ruta: Path, nombre: str | None = None) -> int:
    ruta = Path(ruta)
    contenido = ruta.read_bytes()
    lineas = leer_archivo(ruta)
    return _preparar(con, lineas, "archivo", nombre or ruta.name, contenido)


def preparar_contenido(con: sqlite3.Connection, contenido: bytes, nombre: str) -> int:
    """Para archivos subidos desde la web: se guardan primero y se leen desde el archivo."""
    if not nombre.lower().endswith((".docx", ".txt", ".md")):
        raise ErrorValidacion("Formato no soportado. Usa un archivo .docx o .txt, o pega el texto.")
    if not contenido:
        raise ErrorValidacion("El archivo está vacío.")
    copia = _archivar(contenido, nombre)
    try:
        lineas = leer_archivo(copia)
    except Exception as e:  # archivo dañado o con formato inesperado
        copia.unlink(missing_ok=True)
        raise ErrorValidacion(f"No se pudo leer el archivo: {e}") from e
    return _preparar(con, lineas, "archivo", nombre, contenido, copia)


def preparar_texto(con: sqlite3.Connection, texto: str) -> int:
    if not texto.strip():
        raise ErrorValidacion("No hay texto para importar.")
    return _preparar(con, leer_texto(texto), "texto", "texto pegado", texto.encode("utf-8"))


def _preparar(con: sqlite3.Connection, lineas: list[str], origen: str, nombre: str,
              contenido: bytes, copia: Path | None = None) -> int:
    ctx = construir_contexto(con)
    interpretadas = interprete.interpretar(lineas, ctx)
    if not any(i.clase in ("movimiento", "sin_valor") for i in interpretadas):
        raise ErrorValidacion("No se encontró ningún movimiento en el contenido.")
    copia = copia or _archivar(contenido, nombre if origen == "archivo" else "texto_pegado.txt")
    ocurrencias: Counter = Counter()
    with transaccion(con):
        lote_id = con.execute(
            "INSERT INTO lotes_importacion (origen, archivo_nombre, archivo_hash, ruta_copia) VALUES (?, ?, ?, ?)",
            (origen, nombre, hashlib.sha256(contenido).hexdigest(), str(copia)),
        ).lastrowid
        for i in interpretadas:
            if i.clase in ("vacia", "fecha"):
                continue
            clave = (i.fecha, normalizar(i.texto))
            huella = _huella(i.fecha, i.texto, ocurrencias[clave])
            ocurrencias[clave] += 1
            origen_campos = dict(i.origen, clase=i.clase)
            con.execute(
                """INSERT INTO lineas_importacion
                   (lote_id, num_linea, texto, fecha_contexto, huella, estado, tipo, fecha, valor,
                    descripcion, cuenta_id, categoria_id, meta_id, observaciones, origen_campos, notas)
                   VALUES (?, ?, ?, ?, ?, 'revisar', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lote_id, i.num_linea, i.texto.strip(),
                 i.fecha_contexto.isoformat() if i.fecha_contexto else None, huella,
                 i.tipo, i.fecha.isoformat() if i.fecha else None, i.valor, i.descripcion,
                 i.cuenta_id, i.categoria_id, i.meta_id, i.observaciones,
                 json.dumps(origen_campos, ensure_ascii=False), json.dumps(i.notas, ensure_ascii=False)),
            )
        revalidar(con, lote_id)
    return lote_id


# ------------------------------------------------------------------ validación

def _obtener_lote(con: sqlite3.Connection, lote_id: int) -> sqlite3.Row:
    lote = con.execute("SELECT * FROM lotes_importacion WHERE id = ?", (lote_id,)).fetchone()
    if not lote:
        raise ErrorValidacion(f"No existe el lote de importación #{lote_id}.")
    return lote


def _problemas(con: sqlite3.Connection, linea: sqlite3.Row,
               reclamados: set[int]) -> tuple[list[dict], list[dict]]:
    errores: list[str] = []
    avisos: list[str] = []
    if not linea["fecha"]:
        errores.append("Falta la fecha: escribe una línea con la fecha (p. ej. 16/11) encima de este movimiento.")
    if linea["valor"] is None:
        errores.append("Falta el valor.")
    if not linea["cuenta_id"]:
        errores.append("Falta la cuenta.")
    if linea["tipo"] in ("gasto", "ingreso") and not linea["categoria_id"]:
        errores.append("Falta la categoría.")
    if not (linea["descripcion"] or "").strip():
        errores.append("Falta la descripción.")
    if not errores:
        datos = movimientos.DatosMovimiento(
            tipo=linea["tipo"], fecha=linea["fecha"], valor=linea["valor"],
            descripcion=linea["descripcion"], cuenta_id=linea["cuenta_id"],
            categoria_id=linea["categoria_id"], meta_id=linea["meta_id"],
        )
        errores.extend(movimientos.validar(con, datos))
    if not errores:
        if linea["valor"] < 1000:
            avisos.append(f"El valor {cop(linea['valor'])} es inusualmente bajo.")
        for dup in movimientos.posibles_duplicados(con, linea["tipo"], linea["fecha"], linea["valor"], reclamados):
            avisos.append(
                f"Posible duplicado de #{dup['id']}: «{dup['descripcion']}» {cop(dup['valor'])} "
                f"del {fecha_corta(dup['fecha'])} ({dup['cuenta']})."
            )
        # ¿Línea editada en el archivo después de importarla? Un movimiento importado antes,
        # con la misma descripción y fecha pero otro valor, que ya no aparece en el archivo.
        parecidos = con.execute(
            "SELECT id, valor FROM movimientos WHERE eliminado_en IS NULL AND origen = 'importacion' "
            "AND tipo = ? AND fecha = ? AND valor <> ?",
            (linea["tipo"], linea["fecha"], linea["valor"]),
        ).fetchall()
        desc = normalizar(linea["descripcion"])
        for p in parecidos:
            if p["id"] in reclamados:
                continue
            otro = movimientos.obtener(con, p["id"])
            if normalizar(otro["descripcion"]) == desc:
                avisos.append(
                    f"Ya existe «{otro['descripcion']}» ese día por {cop(otro['valor'])} (#{otro['id']}). "
                    "¿Modificaste esta línea después de importarla?"
                )
    return ([{"nivel": "error", "texto": t} for t in errores],
            [{"nivel": "aviso", "texto": t} for t in avisos])


def revalidar(con: sqlite3.Connection, lote_id: int) -> None:
    """Recalcula el estado de cada línea de un lote en borrador."""
    lote = _obtener_lote(con, lote_id)
    if lote["estado"] != "borrador":
        return
    lineas = con.execute(
        "SELECT * FROM lineas_importacion WHERE lote_id = ? ORDER BY num_linea", (lote_id,)
    ).fetchall()
    with transaccion(con):
        reclamados: set[int] = set()
        pendientes = []
        for l in lineas:
            existente = con.execute(
                "SELECT id FROM movimientos WHERE huella = ? AND eliminado_en IS NULL", (l["huella"],)
            ).fetchone()
            if existente:
                reclamados.add(existente["id"])
                con.execute(
                    "UPDATE lineas_importacion SET estado = 'ya_importada', movimiento_id = ?, problemas = '[]' "
                    "WHERE id = ?", (existente["id"], l["id"]))
            else:
                pendientes.append(l)
        for l in pendientes:
            clase = json.loads(l["origen_campos"]).get("clase")
            if l["descartada_usuario"]:
                estado, problemas = "descartada", []
            elif clase == "sin_valor" and l["valor"] is None:
                estado, problemas = "ignorada", []
            else:
                errores, avisos = _problemas(con, l, reclamados)
                problemas = errores + avisos
                if errores or (avisos and not l["advertencias_ok"]):
                    estado = "revisar"
                else:
                    estado = "lista"
            con.execute(
                "UPDATE lineas_importacion SET estado = ?, problemas = ?, movimiento_id = NULL WHERE id = ?",
                (estado, json.dumps(problemas, ensure_ascii=False), l["id"]),
            )


# ------------------------------------------------------------------- corregir

def corregir(con: sqlite3.Connection, lote_id: int, num_linea: int, cambios: dict | None = None,
             aceptar_avisos: bool | None = None, descartar: bool | None = None,
             recordar_patron: str | None = None) -> None:
    lote = _obtener_lote(con, lote_id)
    if lote["estado"] != "borrador":
        raise ErrorValidacion("Solo se pueden corregir lotes en borrador.")
    linea = con.execute(
        "SELECT * FROM lineas_importacion WHERE lote_id = ? AND num_linea = ?", (lote_id, num_linea)
    ).fetchone()
    if not linea:
        raise ErrorValidacion(f"El lote #{lote_id} no tiene la línea {num_linea}.")
    if linea["estado"] == "ya_importada":
        raise ErrorValidacion("Esta línea ya fue importada antes; corrígela desde Movimientos.")

    cambios = {k: v for k, v in (cambios or {}).items() if k in CAMPOS_EDITABLES}
    if "tipo" in cambios and cambios["tipo"] not in movimientos.TIPOS:
        raise ErrorValidacion("Tipo no válido.")
    origen = json.loads(linea["origen_campos"])
    modificados = {k: v for k, v in cambios.items() if linea[k] != v}
    if modificados.get("tipo") and modificados["tipo"] != linea["tipo"]:
        # Al cambiar el tipo, la categoría anterior deja de tener sentido.
        if "categoria_id" not in modificados:
            modificados["categoria_id"] = None
        if modificados["tipo"] != "ahorro":
            modificados["meta_id"] = None
    for k in modificados:
        origen[k] = "manual"

    with transaccion(con):
        if modificados:
            asignaciones = ", ".join(f"{k} = ?" for k in modificados)
            con.execute(
                f"UPDATE lineas_importacion SET {asignaciones}, advertencias_ok = 0, origen_campos = ? WHERE id = ?",
                (*modificados.values(), json.dumps(origen, ensure_ascii=False), linea["id"]),
            )
        if aceptar_avisos is not None:
            con.execute("UPDATE lineas_importacion SET advertencias_ok = ? WHERE id = ?",
                        (int(aceptar_avisos), linea["id"]))
        if descartar is not None:
            con.execute("UPDATE lineas_importacion SET descartada_usuario = ? WHERE id = ?",
                        (int(descartar), linea["id"]))
        if recordar_patron is not None:
            patron = normalizar(recordar_patron) or None
            con.execute("UPDATE lineas_importacion SET recordar_patron = ? WHERE id = ?", (patron, linea["id"]))
        revalidar(con, lote_id)


def aceptar_todos_los_avisos(con: sqlite3.Connection, lote_id: int) -> int:
    """Acepta los avisos (no los errores) de todas las líneas en revisión."""
    lote = _obtener_lote(con, lote_id)
    if lote["estado"] != "borrador":
        raise ErrorValidacion("Solo se pueden modificar lotes en borrador.")
    aceptadas = 0
    with transaccion(con):
        for l in lineas_del_lote(con, lote_id):
            if l["estado"] == "revisar" and l["problemas"] and all(p["nivel"] == "aviso" for p in l["problemas"]):
                con.execute("UPDATE lineas_importacion SET advertencias_ok = 1 WHERE id = ?", (l["id"],))
                aceptadas += 1
        revalidar(con, lote_id)
    return aceptadas


# -------------------------------------------------- confirmar / descartar / revertir

def confirmar(con: sqlite3.Connection, lote_id: int) -> dict:
    lote = _obtener_lote(con, lote_id)
    if lote["estado"] != "borrador":
        raise ErrorValidacion(f"El lote #{lote_id} está {lote['estado']}; solo se confirman borradores.")
    revalidar(con, lote_id)
    lineas = lineas_del_lote(con, lote_id)
    por_revisar = [l for l in lineas if l["estado"] == "revisar"]
    if por_revisar:
        raise ErrorValidacion(
            [f"Quedan {len(por_revisar)} líneas por revisar: "
             + ", ".join(str(l["num_linea"]) for l in por_revisar[:15])
             + ("…" if len(por_revisar) > 15 else "") + "."]
        )
    listas = [l for l in lineas if l["estado"] == "lista"]
    if not listas:
        raise ErrorValidacion("No hay líneas nuevas para importar.")

    respaldo = crear_respaldo(con, motivo=f"antes-de-importar-lote-{lote_id}")
    with transaccion(con):
        for l in listas:
            mov_id = movimientos.crear(
                con,
                movimientos.DatosMovimiento(
                    tipo=l["tipo"], fecha=l["fecha"], valor=l["valor"], descripcion=l["descripcion"],
                    cuenta_id=l["cuenta_id"], categoria_id=l["categoria_id"], meta_id=l["meta_id"],
                    observaciones=l["observaciones"],
                ),
                origen="importacion", lote_id=lote_id, huella=l["huella"],
            )
            con.execute("UPDATE lineas_importacion SET estado = 'importada', movimiento_id = ? WHERE id = ?",
                        (mov_id, l["id"]))
            if l["recordar_patron"] and l["categoria_id"]:
                catalogos.guardar_regla(con, l["recordar_patron"], l["tipo"], l["categoria_id"])
        con.execute(
            "UPDATE lotes_importacion SET estado = 'confirmado', "
            "confirmado_en = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime') WHERE id = ?",
            (lote_id,),
        )
    return {"importados": len(listas), "total": sum(l["valor"] for l in listas), "respaldo": respaldo}


def descartar_lote(con: sqlite3.Connection, lote_id: int) -> None:
    lote = _obtener_lote(con, lote_id)
    if lote["estado"] != "borrador":
        raise ErrorValidacion("Solo se pueden descartar lotes en borrador.")
    with transaccion(con):
        con.execute("UPDATE lotes_importacion SET estado = 'descartado' WHERE id = ?", (lote_id,))


def revertir(con: sqlite3.Connection, lote_id: int) -> int:
    """Deshace un lote confirmado (borrado lógico de sus movimientos). Devuelve cuántos se revirtieron."""
    lote = _obtener_lote(con, lote_id)
    if lote["estado"] != "confirmado":
        raise ErrorValidacion("Solo se pueden revertir lotes confirmados.")
    crear_respaldo(con, motivo=f"antes-de-revertir-lote-{lote_id}")
    with transaccion(con):
        n = con.execute(
            "UPDATE movimientos SET eliminado_en = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime') "
            "WHERE lote_id = ? AND eliminado_en IS NULL",
            (lote_id,),
        ).rowcount
        con.execute(
            "UPDATE lotes_importacion SET estado = 'revertido', "
            "revertido_en = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime') WHERE id = ?",
            (lote_id,),
        )
    return n


# --------------------------------------------------------------------- consulta

def lineas_del_lote(con: sqlite3.Connection, lote_id: int) -> list[dict]:
    filas = con.execute(
        """SELECT l.*, cu.nombre AS cuenta, c.nombre AS categoria, cp.nombre AS categoria_padre,
                  ma.nombre AS meta
           FROM lineas_importacion l
           LEFT JOIN cuentas cu ON cu.id = l.cuenta_id
           LEFT JOIN categorias c ON c.id = l.categoria_id
           LEFT JOIN categorias cp ON cp.id = c.padre_id
           LEFT JOIN metas_ahorro ma ON ma.id = l.meta_id
           WHERE l.lote_id = ? ORDER BY l.num_linea""",
        (lote_id,),
    ).fetchall()
    resultado = []
    for f in filas:
        d = dict(f)
        d["problemas"] = json.loads(d["problemas"])
        d["notas"] = json.loads(d["notas"])
        d["origen_campos"] = json.loads(d["origen_campos"])
        d["categoria_completa"] = (f"{d['categoria_padre']} › {d['categoria']}"
                                   if d["categoria_padre"] else d["categoria"])
        palabras = [p for p in normalizar(d["descripcion"]).split() if p.isalpha() and len(p) >= 3]
        d["sugerencia_patron"] = palabras[0] if palabras else ""
        resultado.append(d)
    return resultado


ORDEN_ESTADOS = ("revisar", "lista", "ya_importada", "importada", "ignorada", "descartada")


def resumen_lote(con: sqlite3.Connection, lote_id: int) -> dict:
    lote = dict(_obtener_lote(con, lote_id))
    lineas = lineas_del_lote(con, lote_id)
    conteo = Counter(l["estado"] for l in lineas)
    a_importar = [l for l in lineas if l["estado"] in ("lista", "importada")]
    totales = Counter()
    for l in a_importar:
        totales[l["tipo"]] += l["valor"] or 0
    return {
        "lote": lote,
        "lineas": lineas,
        "conteo": {e: conteo.get(e, 0) for e in ORDEN_ESTADOS},
        "totales": {t: totales.get(t, 0) for t in ("gasto", "ingreso", "ahorro")},
        "puede_confirmar": lote["estado"] == "borrador" and conteo.get("revisar", 0) == 0
                           and conteo.get("lista", 0) > 0,
    }


def listar_lotes(con: sqlite3.Connection, limite: int = 50) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT lo.*,
                  (SELECT COUNT(*) FROM lineas_importacion l WHERE l.lote_id = lo.id
                     AND l.estado IN ('lista', 'importada')) AS movimientos,
                  (SELECT COUNT(*) FROM lineas_importacion l WHERE l.lote_id = lo.id
                     AND l.estado = 'revisar') AS por_revisar,
                  (SELECT COUNT(*) FROM lineas_importacion l WHERE l.lote_id = lo.id
                     AND l.estado = 'ya_importada') AS ya_importadas
           FROM lotes_importacion lo ORDER BY lo.id DESC LIMIT ?""",
        (limite,),
    ).fetchall()
