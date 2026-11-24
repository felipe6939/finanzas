"""Aplicación web local (solo 127.0.0.1)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, config, demo, respaldo
from ..analytics import reporte, resumen
from ..core import formato
from ..core.fechas import Mes, hoy
from ..db.conexion import conectar
from ..db.migrar import migrar
from ..importers import importacion, interprete
from ..services import catalogos, gastos_fijos, movimientos, prestamos, punto_partida, saldos
from ..services.errores import ErrorValidacion
from . import reporte_pdf

DIR = Path(__file__).parent
plantillas = Jinja2Templates(directory=str(DIR / "templates"))
plantillas.env.filters.update(
    cop=formato.cop, cop_corto=formato.cop_corto, fecha=formato.fecha_corta,
    fecha_dia=formato.fecha_dia, pct=formato.porcentaje, miles=formato.miles,
)
plantillas.env.filters["fromjson"] = json.loads


def url_con(request: Request, **cambios) -> str:
    """La URL actual con algunos parámetros cambiados (None los quita)."""
    params = dict(request.query_params)
    for k in ("ok", "error"):
        params.pop(k, None)
    for k, v in cambios.items():
        if v is None:
            params.pop(k, None)
        else:
            params[k] = v
    return request.url.path + ("?" + urlencode(params) if params else "")


plantillas.env.globals.update(version=__version__, url_con=url_con)


def leer_valor(texto: str | None) -> int | None:
    """Interpreta '45.000', '45000', '45k', '$ 1.200.000'. None si no es un valor claro."""
    if not texto or not texto.strip():
        return None
    partes = texto.strip().lower().replace("$", " ").split()
    if len(partes) == 2:
        valor, _, consume = interprete._valor_token(partes[0], partes[1])
        if consume:
            return valor
    valor, _, _ = interprete._valor_token("".join(partes), None)  # '1 200 000' -> 1200000
    return valor


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def ir(url: str, **params) -> RedirectResponse:
    limpios = {k: v for k, v in params.items() if v not in (None, "")}
    return RedirectResponse(url + (("&" if "?" in url else "?") + urlencode(limpios) if limpios else ""),
                            status_code=303)


def crear_app() -> FastAPI:
    app = FastAPI(title="Finanzas personales", docs_url=None, redoc_url=None, openapi_url=None)
    if not config.modo_navegador():  # en el navegador, static/ lo sirve GitHub Pages
        app.mount("/static", StaticFiles(directory=str(DIR / "static")), name="static")

    con_inicial = conectar()
    migrar(con_inicial)
    if not config.modo_navegador():  # en el navegador, el historial de la nube cumple esa función
        respaldo.respaldo_diario(con_inicial)
    con_inicial.close()

    @app.middleware("http")
    async def proteger(request: Request, call_next):
        # Impide que otra página web abierta en el navegador envíe formularios a esta app.
        if request.method not in ("GET", "HEAD"):
            origen = request.headers.get("origin") or request.headers.get("referer")
            if origen:
                host = urlparse(origen).netloc
                if host != request.headers.get("host") and host not in (
                        f"127.0.0.1:{config.PUERTO}", f"localhost:{config.PUERTO}", "testserver"):
                    return Response("Origen no permitido", status_code=403)
        request.state.con = conectar()
        try:
            respuesta = await call_next(request)
        finally:
            request.state.con.close()
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    def render(request: Request, plantilla: str, **ctx) -> HTMLResponse:
        con = request.state.con
        ctx.update(
            request=request,
            demo_activo=demo.demo_activo(con),
            ruta=request.url.path,
            ok=request.query_params.get("ok"),
            error=request.query_params.get("error"),
            hoy=hoy(),
            mes_inicio=Mes.de(catalogos.fecha_inicio(con)),
            # Para el autocompletado de #categoría en los registros rápidos y al importar texto.
            etiquetas_json=json.dumps(interprete.etiquetas_categorias(importacion.categorias_ctx(con)),
                                      ensure_ascii=False).replace("</", "<\\/"),
        )
        return plantillas.TemplateResponse(request, plantilla, ctx)

    def contexto_formulario(con) -> dict:
        return {
            "cuentas": catalogos.listar_cuentas(con, solo_activas=True),
            "categorias_gasto": catalogos.arbol_categorias(con, "gasto"),
            "categorias_ingreso": catalogos.arbol_categorias(con, "ingreso"),
            "metas": catalogos.listar_metas(con, solo_activas=True),
            "fecha_inicio": catalogos.fecha_inicio(con),
        }

    # ------------------------------------------------------------- dashboard

    @app.get("/", response_class=HTMLResponse)
    def inicio(request: Request, mes: str | None = None):
        con = request.state.con
        try:
            m = Mes.de(mes)
        except ValueError:
            m = Mes.de(None)
        m = max(m, Mes.de(catalogos.fecha_inicio(con)))
        k = resumen.kpis(con, m)
        categorias = resumen.gastos_por_categoria(con, m)
        datos_graficos = {
            "evolucion": resumen.evolucion(con, m),
            "categorias": [{"id": c["id"], "nombre": c["nombre"], "total": c["total"],
                            "participacion": c["participacion"], "anterior": c["anterior"],
                            "variacion": c["variacion"]} for c in categorias],
            "ritmo": resumen.ritmo_de_gasto(con, m),
            "mes": m.clave,
            "parcial": k["parcial"],
        }
        return render(
            request, "dashboard.html", mes=m, k=k, categorias=categorias,
            naturaleza=resumen.distribucion(con, m, "naturaleza"),
            necesidad=resumen.distribucion(con, m, "necesidad"),
            insights=resumen.insights(con, m),
            ultimos=movimientos.listar(con, desde=m.inicio, hasta=m.fin, limite=6),
            datos_json=json.dumps(datos_graficos, ensure_ascii=False, default=str).replace("</", "<\\/"),
            inicio_sistema=catalogos.fecha_inicio(con),
            hay_movimientos=con.execute("SELECT 1 FROM movimientos WHERE eliminado_en IS NULL LIMIT 1").fetchone(),
            **contexto_formulario(con),
        )

    # ----------------------------------------------------------- movimientos

    @app.get("/movimientos", response_class=HTMLResponse)
    def lista_movimientos(request: Request, mes: str | None = None, tipo: str | None = None,
                          categoria: str | None = None, cuenta: str | None = None, q: str | None = None,
                          naturaleza: str | None = None, necesidad: str | None = None,
                          lote: str | None = None, todos: str | None = None):
        con = request.state.con
        try:
            m = Mes.de(mes)
        except ValueError:
            m = Mes.de(None)
        m = max(m, Mes.de(catalogos.fecha_inicio(con)))
        filtro_lote = _int(lote)
        ver_todo = todos == "1" or filtro_lote is not None
        filas = movimientos.listar(
            con, desde=None if ver_todo else m.inicio, hasta=None if ver_todo else m.fin,
            tipo=tipo or None, categoria_id=_int(categoria), cuenta_id=_int(cuenta), texto=q,
            naturaleza=naturaleza or None, necesidad=necesidad or None, lote_id=filtro_lote,
        )
        totales = {t: sum(f["valor"] for f in filas if f["tipo"] == t) for t in ("ingreso", "gasto", "ahorro")}
        return render(
            request, "movimientos.html", mes=m, filas=filas, totales=totales, ver_todo=ver_todo,
            f={"tipo": tipo or "", "categoria": _int(categoria), "cuenta": _int(cuenta), "q": q or "",
               "naturaleza": naturaleza or "", "necesidad": necesidad or "", "lote": filtro_lote},
            **contexto_formulario(con),
        )

    # --------------------------------------------------- gastos fijos mensuales

    @app.get("/movimientos/fijos", response_class=HTMLResponse)
    def pagina_gastos_fijos(request: Request, editar: str | None = None):
        con = request.state.con
        lista = gastos_fijos.listar(con)
        defecto = catalogos.cuenta_por_defecto(con)
        return render(request, "gastos_fijos.html", fijos=lista, editar=_int(editar), mes=Mes.de(hoy()),
                      total=sum(g["valor"] for g in lista),
                      registrado=sum(g["valor"] for g in lista if g["este_mes"]),
                      pendientes=sum(1 for g in lista if not g["este_mes"]),
                      cuenta_defecto=defecto["id"] if defecto else None, **contexto_formulario(con))

    @app.post("/movimientos/fijos/{accion}")
    async def acciones_gastos_fijos(request: Request, accion: str):
        con = request.state.con
        form = await request.form()
        volver = "/movimientos/fijos"
        try:
            if accion == "guardar":
                gid = _int(form.get("id"))
                gastos_fijos.guardar(con, gid, form.get("descripcion") or "", leer_valor(form.get("valor")),
                                     _int(form.get("categoria_id")), _int(form.get("cuenta_id")))
                msg = "Gasto fijo actualizado. Los meses ya registrados no cambian." if gid else "Gasto fijo agregado."
            elif accion == "registrar":
                msg = gastos_fijos.registrar_mes(con, int(form.get("id")))
            elif accion == "deshacer":
                msg = gastos_fijos.deshacer_mes(con, int(form.get("id")))
            elif accion == "eliminar":
                gastos_fijos.eliminar(con, int(form.get("id")))
                msg = "Gasto fijo eliminado. Los gastos ya registrados se conservan."
            else:
                return ir(volver, error="Acción desconocida.")
        except (ErrorValidacion, ValueError, TypeError) as e:
            errores = e.errores if isinstance(e, ErrorValidacion) else ["Datos no válidos."]
            return ir(volver, error=" ".join(errores))
        return ir(volver, ok=msg)

    def _datos_desde_form(form) -> movimientos.DatosMovimiento:
        tipo = form.get("tipo", "gasto")
        return movimientos.DatosMovimiento(
            tipo=tipo, fecha=form.get("fecha", ""), valor=leer_valor(form.get("valor")),
            descripcion=(form.get("descripcion") or "").strip(), cuenta_id=_int(form.get("cuenta_id")),
            categoria_id=_int(form.get("categoria_id")) if tipo != "ahorro" else None,
            meta_id=_int(form.get("meta_id")) if tipo == "ahorro" else None,
            naturaleza=form.get("naturaleza") or None if tipo != "ahorro" else None,
            necesidad=form.get("necesidad") or None if tipo == "gasto" else None,
            observaciones=(form.get("observaciones") or "").strip(),
        )

    def _form_mov(request, datos: dict, errores=None, avisos=None, mov_id=None, volver="/movimientos"):
        con = request.state.con
        return render(request, "movimiento_form.html", d=datos, errores=errores or [], avisos=avisos or [],
                      mov_id=mov_id, volver=volver,
                      historial=movimientos.historial(con, mov_id) if mov_id else [],
                      **contexto_formulario(con))

    @app.get("/movimientos/nuevo", response_class=HTMLResponse)
    def nuevo_movimiento(request: Request, tipo: str = "gasto"):
        defecto = catalogos.cuenta_por_defecto(request.state.con)
        return _form_mov(request, {"tipo": tipo if tipo in movimientos.TIPOS else "gasto",
                                   "fecha": hoy().isoformat(), "cuenta_id": defecto["id"] if defecto else None})

    @app.post("/movimientos/rapido")
    async def registro_rapido(request: Request):
        con = request.state.con
        form = await request.form()
        texto = (form.get("texto") or "").strip()
        volver = form.get("volver") or "/"
        if not texto:
            return ir(volver, error="Escribe algo como «45.000 almuerzo».")
        try:
            fecha = date.fromisoformat(form.get("fecha") or hoy().isoformat())
        except ValueError:
            fecha = hoy()
        ctx = importacion.construir_contexto(con)
        r = interprete.interpretar_movimiento(1, texto, fecha, ctx)
        datos = {"tipo": r.tipo or "gasto", "fecha": (r.fecha or fecha).isoformat(),
                 "valor": formato.miles(r.valor) if r.valor else "", "descripcion": r.descripcion,
                 "cuenta_id": r.cuenta_id, "categoria_id": r.categoria_id, "meta_id": r.meta_id,
                 "observaciones": r.observaciones}
        d = movimientos.DatosMovimiento(tipo=datos["tipo"], fecha=datos["fecha"], valor=r.valor,
                                        descripcion=r.descripcion, cuenta_id=r.cuenta_id,
                                        categoria_id=r.categoria_id, meta_id=r.meta_id,
                                        observaciones=r.observaciones)
        errores = movimientos.validar(con, d)
        avisos = list(r.notas)
        if not errores and r.valor:
            avisos += [f"Ya hay un movimiento parecido: «{x['descripcion']}» {formato.cop(x['valor'])} "
                       f"el {formato.fecha_corta(x['fecha'])}." for x in
                       movimientos.posibles_duplicados(con, d.tipo, d.fecha, d.valor)]
        if errores or avisos:
            return _form_mov(request, datos, errores=errores, avisos=avisos, volver=volver)
        movimientos.crear(con, d)
        cat = catalogos.obtener_categoria(con, d.categoria_id) if d.categoria_id else None
        cuenta = catalogos.obtener_cuenta(con, d.cuenta_id)
        return ir(volver, ok=f"Registrado: {d.descripcion} {formato.cop(d.valor)} · "
                             f"{cat['nombre_completo'] if cat else 'Ahorro'} · {cuenta['nombre']} · "
                             f"{formato.fecha_corta(d.fecha)}")

    @app.post("/movimientos/nuevo")
    async def crear_movimiento(request: Request):
        con = request.state.con
        form = await request.form()
        d = _datos_desde_form(form)
        volver = form.get("volver") or "/movimientos"
        errores = movimientos.validar(con, d)
        if not errores and form.get("confirmar_duplicado") != "1":
            dups = movimientos.posibles_duplicados(con, d.tipo, d.fecha, d.valor)
            if dups:
                return _form_mov(request, dict(form), avisos=[
                    f"Ya hay un movimiento parecido: «{x['descripcion']}» {formato.cop(x['valor'])} "
                    f"el {formato.fecha_corta(x['fecha'])}." for x in dups], volver=volver)
        try:
            movimientos.crear(con, d)
        except ErrorValidacion as e:
            return _form_mov(request, dict(form), errores=e.errores, volver=volver)
        return ir(volver, ok=f"Registrado: {d.descripcion} {formato.cop(d.valor)}")

    @app.get("/movimientos/{mov_id}", response_class=HTMLResponse)
    def editar_movimiento(request: Request, mov_id: int):
        m = movimientos.obtener(request.state.con, mov_id)
        if not m:
            return ir("/movimientos", error="El movimiento no existe.")
        datos = dict(m)
        datos["valor"] = formato.miles(m["valor"])
        return _form_mov(request, datos, mov_id=mov_id,
                         volver=request.headers.get("referer") or "/movimientos")

    @app.post("/movimientos/{mov_id}")
    async def guardar_movimiento(request: Request, mov_id: int):
        con = request.state.con
        form = await request.form()
        volver = form.get("volver") or "/movimientos"
        try:
            movimientos.actualizar(con, mov_id, _datos_desde_form(form))
        except ErrorValidacion as e:
            return _form_mov(request, dict(form), errores=e.errores, mov_id=mov_id, volver=volver)
        return ir(volver, ok="Movimiento actualizado. El cambio quedó en el historial.")

    @app.post("/movimientos/{mov_id}/eliminar")
    async def eliminar_movimiento(request: Request, mov_id: int):
        form = await request.form()
        try:
            movimientos.eliminar(request.state.con, mov_id)
        except ErrorValidacion as e:
            return ir("/movimientos", error=e.errores[0])
        return ir(form.get("volver") or "/movimientos", ok="Movimiento eliminado (se conserva en el historial).")

    @app.post("/movimientos/{mov_id}/restaurar")
    async def restaurar_movimiento(request: Request, mov_id: int):
        try:
            movimientos.restaurar(request.state.con, mov_id)
        except ErrorValidacion as e:
            return ir(f"/movimientos/{mov_id}", error=e.errores[0])
        return ir(f"/movimientos/{mov_id}", ok="Movimiento restaurado.")

    # ------------------------------------------------------------ importar

    @app.get("/importar", response_class=HTMLResponse)
    def pagina_importar(request: Request):
        return render(request, "importar.html", lotes=importacion.listar_lotes(request.state.con))

    @app.post("/importar")
    async def subir(request: Request, archivo: UploadFile | None = None, texto: str = Form("")):
        con = request.state.con
        try:
            if archivo is not None and archivo.filename:
                contenido = await archivo.read()
                lote = importacion.preparar_contenido(con, contenido, archivo.filename)
            else:
                lote = importacion.preparar_texto(con, texto)
        except ErrorValidacion as e:
            return ir("/importar", error=e.errores[0])
        return ir(f"/importar/{lote}")

    @app.get("/importar/{lote_id}", response_class=HTMLResponse)
    def revisar_lote(request: Request, lote_id: int, ver: str | None = None):
        con = request.state.con
        try:
            r = importacion.resumen_lote(con, lote_id)
        except ErrorValidacion as e:
            return ir("/importar", error=e.errores[0])
        filtro = ver or ("revisar" if r["conteo"]["revisar"] else "")
        lineas = [l for l in r["lineas"] if not filtro or l["estado"] == filtro]
        return render(request, "lote.html", r=r, lineas=lineas, filtro=filtro, **contexto_formulario(con))

    @app.post("/importar/{lote_id}/linea/{num}")
    async def corregir_linea(request: Request, lote_id: int, num: int):
        con = request.state.con
        form = await request.form()
        accion = form.get("accion", "guardar")
        destino = f"/importar/{lote_id}?ver={form.get('ver', '')}#l{num}"
        try:
            if accion == "descartar":
                importacion.corregir(con, lote_id, num, descartar=True)
            elif accion == "incluir":
                importacion.corregir(con, lote_id, num, descartar=False)
            elif accion == "aceptar":
                importacion.corregir(con, lote_id, num, aceptar_avisos=True)
            else:
                tipo = form.get("tipo")
                cambios = {"tipo": tipo, "fecha": form.get("fecha") or None,
                           "valor": leer_valor(form.get("valor")),
                           "descripcion": (form.get("descripcion") or "").strip() or None,
                           "cuenta_id": _int(form.get("cuenta_id")),
                           "categoria_id": _int(form.get("categoria_id")) if tipo != "ahorro" else None,
                           "meta_id": _int(form.get("meta_id")) if tipo == "ahorro" else None,
                           "observaciones": (form.get("observaciones") or "").strip()}
                if form.get("valor") and cambios["valor"] is None:
                    raise ErrorValidacion(f"No entiendo el valor «{form.get('valor')}».")
                importacion.corregir(con, lote_id, num, cambios,
                                     recordar_patron=(form.get("recordar") or "").strip() or None,
                                     aceptar_avisos=True if form.get("aceptar") else None)
        except ErrorValidacion as e:
            return ir(destino.split("#")[0], error=e.errores[0])
        return RedirectResponse(destino, status_code=303)

    @app.post("/importar/{lote_id}/{accion}")
    async def accion_lote(request: Request, lote_id: int, accion: str):
        con = request.state.con
        try:
            if accion == "confirmar":
                r = importacion.confirmar(con, lote_id)
                return ir(f"/movimientos?lote={lote_id}",
                          ok=f"Importados {r['importados']} movimientos. Se creó un respaldo antes.")
            if accion == "descartar":
                importacion.descartar_lote(con, lote_id)
                return ir("/importar", ok=f"Lote #{lote_id} descartado. No se registró nada.")
            if accion == "revertir":
                n = importacion.revertir(con, lote_id)
                return ir("/importar", ok=f"Lote #{lote_id} revertido: {n} movimientos eliminados.")
            if accion == "aceptar-avisos":
                n = importacion.aceptar_todos_los_avisos(con, lote_id)
                return ir(f"/importar/{lote_id}", ok=f"Avisos aceptados en {n} líneas.")
        except ErrorValidacion as e:
            return ir(f"/importar/{lote_id}", error=e.errores[0])
        return ir(f"/importar/{lote_id}", error="Acción desconocida.")

    # -------------------------------------------------------- configuración

    # --------------------------------------------------------------- cuentas

    @app.get("/cuentas", response_class=HTMLResponse)
    def panel_cuentas(request: Request, vista: str = "mes"):
        con = request.state.con
        vista = "total" if vista == "total" else "mes"
        mes = Mes.de(hoy())
        if vista == "total":
            lista = saldos.saldos(con)
            visibles = [s for s in lista if s["activa"] or s["saldo"]]
            extra = {"total": sum(s["saldo"] for s in lista),
                     "programado": sum(s["programado"] for s in lista),
                     "posicion": resumen.posicion(con, date.max)}
        else:
            lista = saldos.saldos_mes(con, mes)
            visibles = [s for s in lista if s["activa"] or s["inicio"] or s["entradas"] or s["salidas"]]
            extra = {k: sum(s[k] for s in lista)
                     for k in ("inicio", "final", "entradas", "salidas", "programado", "ahorro")}
        return render(
            request, "cuentas.html", vista=vista, mes=mes, saldos=visibles,
            transferencias=saldos.listar_transferencias(con), **extra, **contexto_formulario(con),
        )

    @app.post("/cuentas/{accion}")
    async def acciones_cuentas(request: Request, accion: str):
        con = request.state.con
        form = await request.form()
        volver = "/cuentas?vista=total" if form.get("vista") == "total" else "/cuentas"
        try:
            if accion == "transferir":
                saldos.transferir(con, form.get("fecha", ""), leer_valor(form.get("valor") or ""),
                                  _int(form.get("origen")), _int(form.get("destino")), form.get("nota", ""))
                msg = "Movimiento entre cuentas registrado."
            elif accion == "transferir-eliminar":
                saldos.eliminar_transferencia(con, int(form.get("id")))
                msg = "Movimiento entre cuentas eliminado."
            else:
                return ir(volver, error="Acción desconocida.")
        except ErrorValidacion as e:
            return ir(volver, error=" ".join(e.errores))
        return ir(volver, ok=msg)

    @app.get("/manifest.webmanifest")
    def manifiesto():
        return Response(json.dumps({
            "name": "Finanzas personales", "short_name": "Finanzas", "start_url": "/", "display": "standalone",
            "background_color": "#fcfcfb", "theme_color": "#2a78d6", "lang": "es",
            "icons": [{"src": "/static/icono-180.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "/static/icono-512.png", "sizes": "512x512", "type": "image/png"}],
        }), media_type="application/manifest+json")

    # --------------------------------------------------------------- reporte

    def html_reporte(con, vista_previa: bool) -> str:
        d = reporte.datos(con)
        return plantillas.get_template("reporte.html").render(
            d=d, graficos=reporte_pdf.graficos(d), vista_previa=vista_previa,
            titulo_fecha=reporte.titulo_fecha, colores_naturaleza=reporte_pdf.COLORES_NATURALEZA,
            colores_necesidad=reporte_pdf.COLORES_NECESIDAD,
        ).replace("$ ", "$&nbsp;")  # que el signo no quede solo al final de una línea

    @app.get("/reporte", response_class=HTMLResponse)
    def ver_reporte(request: Request):
        return HTMLResponse(html_reporte(request.state.con, vista_previa=True))

    @app.get("/reporte.pdf")
    def descargar_reporte(request: Request):
        con = request.state.con
        if config.modo_navegador():  # sin Edge: el navegador imprime el reporte («Guardar como PDF»)
            return HTMLResponse(html_reporte(con, vista_previa=False).replace(
                "</body>", "<script>addEventListener('load', () => setTimeout(print, 300));</script></body>"))
        try:
            pdf = reporte_pdf.html_a_pdf(html_reporte(con, vista_previa=False))
        except reporte_pdf.SinNavegador:
            return ir("/", error="No encontré Microsoft Edge ni Google Chrome para crear el PDF. "
                                 "Abre «Ver reporte» y usa Ctrl+P → Guardar como PDF.")
        except Exception:  # noqa: BLE001 — se informa al usuario sin perder la sesión
            return ir("/", error="No se pudo crear el PDF. Abre «Ver reporte» y usa Ctrl+P → Guardar como PDF.")
        nombre = reporte.nombre_archivo(hoy())
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})

    @app.get("/configuracion", response_class=HTMLResponse)
    def configuracion(request: Request):
        con = request.state.con
        return render(
            request, "configuracion.html",
            todas_cuentas=catalogos.listar_cuentas(con),
            arbol_gasto=catalogos.arbol_categorias(con, "gasto"),
            arbol_ingreso=catalogos.arbol_categorias(con, "ingreso"),
            todas_metas=catalogos.listar_metas(con),
            reglas=[r for r in catalogos.listar_reglas(con) if r["origen"] == "usuario"],
            n_reglas_base=sum(1 for r in catalogos.listar_reglas(con) if r["origen"] == "base"),
            respaldos=respaldo.listar_respaldos()[:8],
            ahorro_inicial=catalogos.ahorro_inicial(con),
            me_deben=prestamos.listar(con),
            punto_partida_en_ingresos=punto_partida.ya_aplicado(con),
            total_punto_partida=punto_partida.total(con),
            hoy=hoy(),
            ruta_datos=config.dir_datos(),
            **contexto_formulario(con),
        )

    @app.post("/configuracion/{seccion}")
    async def guardar_configuracion(request: Request, seccion: str):
        con = request.state.con
        form = await request.form()
        try:
            if seccion == "inicio" and punto_partida.ya_aplicado(con):
                # Los saldos ya son ingresos: solo se edita el ahorro inicial (nunca los saldos ni la fecha).
                ahorro_txt = form.get("ahorro_inicial")
                ahorro = leer_valor(ahorro_txt) if ahorro_txt not in (None, "", "0") else 0
                if ahorro is None:
                    raise ErrorValidacion("No entiendo el valor ahorrado.")
                catalogos.actualizar_inicio(con, catalogos.fecha_inicio(con).isoformat(), ahorro)
                msg = "Ahorro inicial guardado."
            elif seccion == "inicio":
                saldos = {}
                for c in catalogos.listar_cuentas(con):
                    texto = form.get(f"saldo_{c['id']}")
                    valor = leer_valor(texto) if texto not in (None, "", "0") else 0
                    if valor is None:
                        raise ErrorValidacion(f"No entiendo el saldo de {c['nombre']}: «{texto}».")
                    saldos[c["id"]] = valor
                ahorro_txt = form.get("ahorro_inicial")
                ahorro = leer_valor(ahorro_txt) if ahorro_txt not in (None, "", "0") else 0
                if ahorro is None:
                    raise ErrorValidacion("No entiendo el ahorro inicial.")
                catalogos.actualizar_inicio(con, form.get("fecha_inicio", ""), ahorro)
                for c in catalogos.listar_cuentas(con):
                    catalogos.guardar_cuenta(con, c["id"], c["nombre"], c["tipo"], c["alias"],
                                             saldos[c["id"]], bool(c["activa"]))
                msg = "Punto de partida guardado."
            elif seccion == "deuda":
                prestamos.guardar_inicial(con, _int(form.get("id")), form.get("persona", ""),
                                          leer_valor(form.get("valor") or ""), form.get("nota", ""))
                msg = "Deuda a tu favor guardada."
            elif seccion == "deuda-eliminar":
                prestamos.eliminar(con, int(form.get("id")))
                msg = "Deuda eliminada."
            elif seccion == "abono":
                prestamos.registrar_abono(con, int(form.get("prestamo_id")), form.get("fecha", ""),
                                          leer_valor(form.get("valor") or ""), _int(form.get("cuenta_id")),
                                          form.get("nota", ""))
                msg = "Pago recibido registrado."
            elif seccion == "abono-eliminar":
                prestamos.eliminar_abono(con, int(form.get("id")))
                msg = "Pago eliminado."
            elif seccion == "cuenta":
                cid = _int(form.get("id"))
                actual = catalogos.obtener_cuenta(con, cid) if cid else None
                catalogos.guardar_cuenta(
                    con, cid, form.get("nombre", ""), form.get("tipo", "banco"), form.get("alias", ""),
                    actual["saldo_inicial"] if actual else (leer_valor(form.get("saldo_inicial")) or 0),
                    activa=form.get("activa", "1") == "1")
                if form.get("por_defecto") == "1" and cid:
                    catalogos.fijar_cuenta_por_defecto(con, cid)
                msg = "Cuenta guardada."
            elif seccion == "defecto":
                catalogos.fijar_cuenta_por_defecto(con, int(form.get("id")))
                msg = "Cuenta por defecto actualizada."
            elif seccion == "categoria":
                cid = _int(form.get("id"))
                catalogos.guardar_categoria(
                    con, cid, form.get("nombre", ""), form.get("tipo", "gasto"), _int(form.get("padre_id")),
                    form.get("naturaleza") or None, form.get("necesidad") or None,
                    activa=form.get("activa", "1") == "1")
                msg = "Categoría guardada."
            elif seccion == "meta":
                catalogos.guardar_meta(con, _int(form.get("id")), form.get("nombre", ""),
                                       leer_valor(form.get("monto_objetivo")), form.get("fecha_objetivo") or None,
                                       activa=form.get("activa", "1") == "1")
                msg = "Meta guardada."
            elif seccion == "regla":
                catalogos.guardar_regla(con, form.get("patron", ""), form.get("tipo", "gasto"),
                                        int(form.get("categoria_id") or 0))
                msg = "Regla guardada."
            elif seccion == "regla-eliminar":
                catalogos.eliminar_regla(con, int(form.get("id")))
                msg = "Regla eliminada."
            elif seccion == "respaldo":
                ruta = respaldo.crear_respaldo(con, "manual")
                msg = f"Respaldo creado: {ruta.name}"
            elif seccion == "exportar":
                ruta = respaldo.exportar(con, form.get("formato", "xlsx"))
                msg = f"Exportado en {ruta}"
            elif seccion == "demo-cargar":
                n = demo.cargar(con)
                return ir("/", ok=f"Cargados {n} movimientos de ejemplo (ficticios).")
            elif seccion == "demo-eliminar":
                n = demo.eliminar(con)
                msg = f"Eliminados {n} movimientos de ejemplo."
            else:
                return ir("/configuracion", error="Sección desconocida.")
        except ErrorValidacion as e:
            return ir("/configuracion", error=" ".join(e.errores))
        return ir("/configuracion", ok=msg)

    return app
