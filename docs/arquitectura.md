# Arquitectura y decisiones

## Tecnología

| Pieza | Elección | Por qué |
|---|---|---|
| Lenguaje | Python 3.14 | Un solo lenguaje para datos, importación, web, PDF y scripts de la Skill. (3.15 es demasiado nuevo para algunas librerías.) |
| Base de datos | SQLite (un archivo) | Transaccional, restricciones de integridad, respaldo trivial, sin servidor. PostgreSQL no aporta nada para un usuario. |
| Web | FastAPI + Jinja2, HTML generado en el servidor | Sin paso de compilación ni segundo lenguaje: mantenible durante años. Escucha solo en 127.0.0.1 (nunca 0.0.0.0). |
| Gráficos | ECharts 5.5.1, copia local en `static/vendor` | Funciona sin internet; los mismos gráficos servirán para los PDF. |
| PDF | HTML/CSS → Edge (o Chrome) headless | Ya está instalado; sin dependencias extra. Gráficos del PDF en SVG generado en Python. |

## Capas

```
web (FastAPI)   CLI (python -m finanzas)   Skill importar-movimientos (usa la CLI)
        \               |                       /
         services (validación + reglas)   importers (lector, intérprete, lotes)
                        |
                analytics/resumen.py  ← fuente única de cifras
                        |
                  SQLite (data/finanzas.db)
```

## Modelo de datos (resumen)

- **cuentas**: Nequi (por defecto), Bancolombia, BBVA, Efectivo. Saldo inicial + palabras clave.
- **categorias**: dos niveles (categoría › subcategoría), por tipo (gasto/ingreso), con
  naturaleza (fijo/variable/extraordinario) y necesidad (esencial/discrecional) por defecto.
- **movimientos**: tipo (gasto/ingreso/ahorro; `transferencia` reservado), fecha, valor
  entero > 0, descripción, cuenta, categoría o meta, naturaleza/necesidad resueltas al
  crear, origen (manual/importación/demo), lote y huella.
- **metas_ahorro**, **reglas_clasificacion** (palabra clave → categoría).
- **lotes_importacion** / **lineas_importacion**: trazabilidad completa de cada importación.
- **auditoria**: triggers registran crear/modificar/eliminar con el antes y el después.
- **prestamos** / **abonos_prestamo** («Me deben»): deudas a favor del usuario (las del punto de
  partida no tienen fecha ni cuenta) y los pagos recibidos, que entran a una cuenta.
- **transferencias**: dinero entre cuentas propias (origen → destino).
- **configuracion**: fecha de inicio, ahorro inicial.

## Definiciones financieras

- Balance = ingresos − gastos. Ahorro = movimientos de tipo ahorro (dinero apartado a propósito).
- Sobrante = balance − ahorro. Tasa de ahorro = ahorro / ingresos.
- Patrimonio líquido = saldos iniciales + ingresos − gastos.
- Disponible = patrimonio líquido − ahorro acumulado (ahorro inicial + aportes).
- Pasar dinero entre cuentas propias se registra en «transferencias» (panel Cuentas): no es
  ingreso ni gasto y no cambia el total; solo el saldo de cada cuenta.
- Saldo de una cuenta = saldo inicial + ingresos − gastos − préstamos + pagos recibidos ± transferencias.
  El ahorro no saca dinero de la cuenta (lo marca como apartado). Σ saldos = patrimonio líquido.
- Panel Cuentas con dos submódulos: «Cuentas <mes actual>» (`saldos.saldos_mes`: saldo al empezar el mes,
  entradas, salidas y saldo al final, con los programados del mes) y «Cuentas Total» (`saldos.saldos`:
  todos los meses). El final de un mes es el inicio del siguiente.
- «Me deben» no es disponible ni ingreso. Un pago recibido vuelve a la cuenta (sube el
  disponible) y reduce lo que te deben; no cuenta como ingreso del mes.
- El mes en curso se compara con el mes anterior hasta el mismo día.
- Punto de partida: a pedido del usuario, los saldos iniciales se registraron como ingresos
  «Punto de partida» del primer mes (`punto-partida-a-ingresos`, una sola vez; saldos quedan en 0).
- El tablero no navega antes del mes de inicio; la evolución va del mes de inicio a diciembre
  del año siguiente (meses futuros vacíos salvo que tengan programados).
- Movimientos con fecha futura («programados», hasta 1 año): a pedido del usuario cuentan en
  el disponible desde que se registran. El mes en curso se mide hasta hoy o hasta el último
  programado del mes, y se compara con el mes anterior hasta ese mismo día. Sin año, el
  intérprete elige la fecha más cercana a hoy (pasada o futura).
- Las proyecciones extrapolan solo los gastos variables y se etiquetan como proyección.

## Integridad

- Valores enteros positivos, fechas ISO, categorías del tipo correcto: validado en código Y en la base.
- No hay borrado físico de movimientos reales (trigger). Borrado lógico con `eliminado_en`.
- Importación: copia inmutable del original, huella por línea con índice único, revisión
  obligatoria de lo ambiguo, confirmación todo-o-nada, reversión por lote.
- Respaldos con la API de copia de SQLite + `integrity_check`: diario, antes de importar,
  revertir, migrar, restaurar o reiniciar. Rotación: 30 recientes + último de cada mes.
- Migraciones versionadas (`PRAGMA user_version`), cada una en una transacción.
- La web rechaza formularios enviados desde otros sitios (verificación de Origin).

## Acceso desde el iPhone

- El acceso por Tailscale (`acceso.py`) se eliminó el 23 nov 2026: no funcionaba en el PC.
- Reemplazo en desarrollo: la misma app corriendo en el navegador (Pyodide) publicada en GitHub Pages,
  con los datos cifrados en un repo privado. Plan y fases: `docs/plan-github-pages.md`.

## Hoja de ruta

- [x] Fase 0 — Cimientos: entorno, esquema, migraciones, auditoría, respaldos, pruebas.
- [x] Fase 1 — Núcleo: cuentas, categorías, movimientos, registro rápido, filtros, edición auditada.
- [x] Fase 2 — Importación: intérprete local, lotes, revisión web y CLI, Skill.
- [~] Fase 3 — Dashboard (hecho) · presupuestos · obligaciones recurrentes (pendiente).
- [ ] Fase 4 — Deudas y metas de ahorro completas.
- [~] Fase 5 — Análisis ampliado e informes PDF para celular. Hecho: reporte PDF con corte a hoy
  (`analytics/reporte.py` cifras, `web/reporte_pdf.py` gráficos SVG + impresión, `/reporte` y `/reporte.pdf`).
  El reporte suma hasta la fecha de corte; lo programado se lista aparte («Lo que viene»).
  Se genera en una carpeta temporal con perfil de navegador temporal y no se guarda copia.
- [ ] Futuro — respaldo cifrado en OneDrive, acceso móvil (GitHub Pages + PWA, en curso), proyecciones y escenarios.
