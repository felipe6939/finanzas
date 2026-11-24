---
name: importar-movimientos
description: Importa movimientos financieros (gastos, ingresos, ahorro) desde un archivo .docx/.txt o texto pegado al sistema de finanzas personales, con revisión y aprobación del usuario. Úsala cuando el usuario entregue un archivo o texto con movimientos para registrar, o pida "importar", "cargar mis gastos" o "registrar estos movimientos".
---

# Importar movimientos

Flujo seguro para llevar movimientos al sistema. **Tú interpretas y conversas; el
código valida y escribe.** Nunca escribas en la base de datos directamente (ni con
SQL ni con Python ad hoc): usa SOLO los comandos `importar` de la CLI.

Todos los comandos se ejecutan desde la raíz del proyecto:

```
.venv\Scripts\python.exe -m finanzas importar <acción> ...
```

## Reglas no negociables

1. **No inventes datos.** Si una línea no tiene fecha, valor o una categoría clara,
   pregunta al usuario. No deduzcas valores "probables" ni fechas "razonables".
2. **No confirmes sin aprobación explícita** del usuario después de mostrarle el resumen.
3. **Privacidad (opción C acordada):** el intérprete local procesa el archivo. Tú solo
   necesitas ver las líneas pendientes: usa `ver <lote> --pendientes`. No leas el
   archivo completo ni la base de datos salvo que el usuario lo pida.
4. **Nunca uses `--aceptar` sobre un posible duplicado sin preguntar.** El usuario decide
   si es el mismo movimiento (descartar) o uno distinto (aceptar).
5. Si algo falla, informa el error tal cual. No lo "arregles" saltándote la validación.

## Flujo

1. **Preparar el lote** (crea un borrador; no registra nada):
   - Archivo: `importar preparar "<ruta>"`
   - Texto pegado por el usuario en el chat: guárdalo primero en un .txt en la carpeta
     temporal (scratchpad) y usa `importar preparar "<ruta.txt>" --texto`.
     Si el texto no sigue el formato (ver abajo), primero reescríbelo AL FORMATO,
     línea por línea, **sin cambiar ningún valor, fecha ni descripción**, y muéstrale
     al usuario la versión reescrita antes de prepararla.
2. **Ver lo pendiente**: `importar ver <lote> --pendientes`
   (`--json` si necesitas procesarlo). Las líneas `ya_importada` se omiten solas:
   el usuario puede reimportar el mismo archivo creciente sin duplicar nada.
3. **Resolver cada línea por revisar**, preguntando al usuario de forma agrupada y concreta:
   - Falta categoría → propón 1–2 opciones de `categorias` y pregunta. Al corregir,
     sugiere `--recordar "<palabra>"` para que la próxima vez se clasifique sola.
   - Varios valores posibles / valor bajo → pregunta cuál es el valor.
   - Posible duplicado → pregunta si es el mismo movimiento (→ `--descartar`) o
     uno distinto (→ `--aceptar`).
   - Sin fecha → pregunta la fecha.
   Comando: `importar corregir <lote> <linea> [--valor N] [--fecha AAAA-MM-DD]
   [--categoria "Transporte › Gasolina"] [--cuenta nequi] [--tipo gasto|ingreso|ahorro]
   [--descripcion "..."] [--aceptar] [--descartar] [--recordar "palabra"]`
4. **Mostrar el resumen** (`importar ver <lote>`): cuántos movimientos, totales por tipo,
   cuántos ya estaban registrados y cuántas líneas se ignoran. Pide aprobación.
5. **Confirmar** solo con aprobación: `importar confirmar <lote>`. Se crea un respaldo antes.
   Si el usuario no aprueba: `importar descartar <lote>`.
6. Indica que puede ver el resultado en la app (`iniciar.bat`, sección Movimientos o Inicio).

Corrección posterior: `importar revertir <lote>` deshace un lote completo (borrado lógico
auditado); luego se puede volver a importar el archivo corregido.

## Formato que entiende el intérprete local

```
16/11
45.000 almuerzo
12k bus
gasolina 80.000 bancolombia
+3.200.000 salario bbva
ahorro 300.000 viaje
25.500 farmacia #salud (para mamá)
```

- Línea con solo una fecha → aplica a las siguientes. `17/11 45.000 almuerzo` → solo esa línea.
- Sin signo = gasto · `+` = ingreso · `ahorro` = ahorro.
- Valor: `45.000`, `45000`, `45k`, `45 mil`, `20 lucas`, `1,2 millones`.
- Cuenta: `nequi`, `bancolombia`, `bbva`, `efectivo` (por defecto: Nequi).
- `#categoria` fuerza la categoría · `(texto)` es una observación.

## Comandos auxiliares

- `.venv\Scripts\python.exe -m finanzas categorias` — categorías disponibles.
- `.venv\Scripts\python.exe -m finanzas cuentas` — cuentas y alias.
- `.venv\Scripts\python.exe -m finanzas importar lotes` — historial de importaciones.
