# Finanzas personales — reglas del proyecto

Sistema local de finanzas personales (COP, Colombia). Python 3.14 + SQLite + FastAPI
con plantillas Jinja2 y ECharts (local). Arquitectura y decisiones: `docs/arquitectura.md`.

## Privacidad e integridad (prioridad máxima)

- `data/` contiene la información financiera REAL del usuario. **No leas `data/finanzas.db`,
  los respaldos ni `data/importaciones/` salvo que el usuario lo pida explícitamente.**
- Para desarrollar y probar usa datos ficticios: las pruebas usan una carpeta temporal
  (`FINANZAS_DATOS`) y nunca tocan `data/`. Para revisar la UI, levanta la app con
  `FINANZAS_DATOS=<carpeta temporal>` y `python -m finanzas demo cargar`.
- Nunca escribas en la base con SQL directo. Usa los servicios (`finanzas/services`,
  `finanzas/importers/importacion.py`) o la CLI. Para importar: Skill `importar-movimientos`.
- Los movimientos no se borran físicamente (trigger lo impide); se usa `eliminado_en`.
- Cambios de esquema: SOLO mediante una nueva migración en `finanzas/db/migraciones/`
  registrada en `finanzas/db/migrar.py`. Nunca edites una migración ya aplicada.
- No inventes ingresos, gastos, saldos ni categorías del usuario.

## Estructura

- `finanzas/core` — formato COP/fechas, periodos, normalización de texto.
- `finanzas/db` — conexión, transacciones, migraciones versionadas.
- `finanzas/services` — reglas de negocio (movimientos, catálogos, préstamos «Me deben», saldos por cuenta y transferencias, gastos fijos mensuales: se registran con un clic, una vez por mes). Validan todo.
- `finanzas/importers` — lector (.docx/.txt), intérprete local, lotes de importación.
- `finanzas/analytics/resumen.py` — FUENTE ÚNICA de cifras (dashboard, PDF, Skill).
  `analytics/reporte.py` arma el reporte PDF con corte a hoy usando esas definiciones.
- `finanzas/web` — app web (solo 127.0.0.1:8765). `web/reporte_pdf.py`: gráficos SVG y HTML→PDF con Edge/Chrome.
- `finanzas/cli.py` — comandos `python -m finanzas ...`.
- `finanzas/acceso.py` — acceso desde el iPhone por Tailscale con clave (guía `docs/acceso-iphone.md`).
  `data/acceso.json` guarda el hash de la clave: no leerlo ni mostrarlo. Nunca escuchar en 0.0.0.0.
- `finanzas/respaldo.py`, `finanzas/demo.py` — respaldos/exportación, datos de ejemplo/reinicio.

## Comandos

- Pruebas: `.venv\Scripts\python.exe -m pytest -q` (deben pasar todas antes de dar algo por terminado).
- App: `iniciar.bat` o `.venv\Scripts\python.exe -m finanzas iniciar`.
- CLI: `.venv\Scripts\python.exe -m finanzas --help`.

## Convenciones

- Valores en pesos enteros (INTEGER). Mostrar con `formato.cop()` → `$ 1.250.000`.
- Tipos de movimiento expuestos: gasto, ingreso, ahorro. `transferencia` está reservado en el esquema.
- Comparaciones del mes en curso: contra el mes anterior HASTA EL MISMO DÍA.
- Proyecciones: siempre etiquetadas como tales, con su método.
- Código y textos de interfaz en español.
