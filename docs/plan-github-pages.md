# Plan: versión GitHub Pages con sincronización cifrada (opción A)

Fecha: 23 nov 2026 · Estado: **aprobado**; fase 0 hecha (commit `d336f81`, etiqueta `v-pc-original`); fases 1, 2 y 3 hechas (la app corre en el navegador; datos cifrados con clave en el dispositivo).

Decisiones del usuario (23 nov 2026):
- Tailscale y `acceso.py` se eliminan (fase 1); ya no se usarán.
- Nunca se edita a la vez en PC e iPhone: ante un choque, la app avisa y se detiene (sin fusión automática).
- Autenticación: clave única (sin usuario). Face ID opcional más adelante.
Respaldo previo: `Desktop\Finanzas personales - respaldo 2026-11-23` (copia idéntica verificada).

## Idea central

No reescribir la app: **ejecutar el mismo código Python dentro del navegador** con Pyodide.

```
GitHub Pages (repo público: solo código)          Repo PRIVADO "finanzas-datos"
┌──────────────────────────────────────┐          ┌───────────────────────────┐
│ index.html + arranque.js             │          │ finanzas.db.cifrado       │
│   └─ Pyodide (Python 3.12/3.13)      │  subir   │ (AES-256, un commit por   │
│        └─ finanzas/ (igual que hoy)  │ ───────▶ │  sincronización = historial│
│             crear_app() de FastAPI   │ ◀─────── │  completo de versiones)   │
│             services, analytics...   │  bajar   └───────────────────────────┘
│        └─ SQLite en memoria          │                     ▲
│ IndexedDB: copia cifrada local       │                     │ finanzas nube subir/bajar
└──────────────────────────────────────┘          App del PC (la de hoy, intacta)
```

- `app.py`, plantillas, `services/`, `analytics/`, `importers/` y las migraciones se usan **tal cual**.
- Un "enrutador" pequeño en JavaScript intercepta los clics y formularios y se los entrega a
  FastAPI dentro del navegador (llamada ASGI directa, sin servidor). El HTML que devuelve se pinta
  en pantalla. Para la app, todo se ve igual que hoy.
- La app del PC sigue existiendo y funcionando como hoy; se le agrega `finanzas nube` para
  sincronizar con la misma copia en la nube (así la Skill `importar-movimientos` sigue sirviendo).

## Lo que cambia (solo en modo navegador)

| Función | Hoy (PC) | Navegador |
|---|---|---|
| Reporte PDF | Edge headless | Página del reporte + «Imprimir → Guardar como PDF» |
| Acceso con clave | `acceso.py` + Tailscale | Clave que descifra los datos (sin ella no hay nada que leer) |
| Respaldos diarios | archivos en `data/respaldos` | historial de GitHub + copia local + exportación manual |
| Primer arranque | inmediato | ~10–20 MB la primera vez (Pyodide); después queda en caché |

## Seguridad

- Cifrado AES-256-GCM con WebCrypto; llave derivada de tu clave (PBKDF2-SHA256, 600.000 iteraciones,
  sal aleatoria). GitHub solo guarda bytes ilegibles.
- Token de GitHub **fine-grained**, limitado al repo `finanzas-datos`, permiso solo «Contents».
  Se guarda cifrado con tu clave en cada dispositivo. Nunca va al repo público ni al chat.
- El repo público se audita antes de cada publicación: sin `data/`, sin `acceso.json`, sin cifras
  reales en pruebas o documentos, sin `.claude/`.
- **Si olvidas la clave, nadie puede recuperar los datos** (ni yo, ni GitHub). Por eso: anotarla en
  un lugar seguro y mantener la exportación periódica al PC.

## Anti pérdida y sincronización

1. **Tres copias**: repo privado con historial, copia cifrada en cada dispositivo (IndexedDB),
   exportación descargable (+ aviso si llevas más de 7 días sin exportar).
2. **Nunca subir algo dañado**: `PRAGMA integrity_check` antes de cada subida; si falla, no se sube.
3. **Nunca pisar a ciegas**: cada subida envía el identificador (sha) de la versión que se bajó.
   Si otro dispositivo subió algo entre tanto, GitHub la rechaza y la app:
   baja la versión nueva → **re-aplica tus operaciones pendientes** (cada formulario enviado queda en
   un registro de operaciones; los servicios vuelven a validarlas) → sube el resultado.
4. **Sin internet**: registras normal; las operaciones quedan en cola y se suben al reconectar.
5. **Indicador visible**: «Sincronizado hace 2 min» / «3 cambios sin subir» / «Error: …».
6. **Restaurar**: pantalla para ver versiones anteriores del historial y volver a una.
7. **Freno de seguridad**: si una subida tendría menos movimientos que la versión de la nube,
   pide confirmación explícita.
8. Tamaño: la base hoy pesa ~230 KB; comprimida y cifrada ~60 KB por versión. Subidas agrupadas
   (al terminar de editar o cada pocos minutos) → historial de pocos cientos de MB al año, dentro
   de los límites de GitHub.

## Fases

| # | Fase | Resultado verificable |
|---|---|---|
| 0 | **Preparación** — respaldo (hecho), primer commit local del estado actual con etiqueta `v-pc-original`; comprobar que GitHub abre desde el PC corporativo y el iPhone | Punto de retorno garantizado |
| 1 | **Compatibilidad** — correr pruebas; ajustar lo que use Python 3.14 y no exista en Pyodide; aislar Edge/Tailscale/respaldos en archivos detrás de un "modo navegador" | Todas las pruebas pasan; nada cambia en el PC |
| 2 | **Motor en el navegador** — carpeta `navegador/`: arranque de Pyodide, empaquetado de `finanzas/`, enrutador ASGI, PWA instalable en el iPhone | La app corre con datos de demo en el navegador |
| 3 | **Clave y cifrado** — pantalla de clave, cifrado/descifrado, copia local en IndexedDB | Recargar la página conserva los datos; sin clave no se leen |
| 4 | **Sincronización** — conexión al repo privado, bajar al abrir, subir agrupado, conflictos con re-aplicación, cola sin internet, indicador | Dos navegadores editando a la vez terminan con los mismos datos |
| 5 | **Anti pérdida** — integrity_check, freno de seguridad, exportar/importar, aviso de exportación, restaurar versiones | Pruebas de corrupción, borrado y restauración pasan |
| 6 | **App del PC** — `finanzas nube estado/bajar/subir` con el mismo formato cifrado | PC y navegador ven las mismas cifras |
| 7 | **Publicación** — GitHub Actions que arma y publica la página; auditoría de contenido público | URL `https://<usuario>.github.io/<repo>/` funcionando |
| 8 | **Tus datos reales** — solo con tu visto bueno: subir la base actual desde el PC, cifrada | Movimientos, saldos y «Me deben» coinciden en PC e iPhone |
| 9 | **Prueba final** — instalar en el iPhone, modo avión, conflicto real, restauración | Lista de chequeo firmada por ti |

Todo el desarrollo y las pruebas (fases 1–7) usan **datos de demo**. Tus datos reales solo se tocan
en la fase 8, con tu aprobación explícita.

## Lo que necesito de ti (cuando lleguemos)

- Cuenta de GitHub (fase 0), crear dos repos: `finanzas` (público) y `finanzas-datos` (privado).
- Crear el token fine-grained (te guío paso a paso; lo escribes tú directamente en la app).
- Elegir la clave de cifrado y guardarla en un lugar seguro.
- Probar en el iPhone y en el PC corporativo.

## Formato del archivo cifrado (fase 3, `navegador/boveda.js`)

El mismo archivo se guarda en el dispositivo (IndexedDB, clave `base`), se subirá al repo privado
(fase 4) y lo leerá la app del PC (fase 6):

```
FINANZAS-CIFRADO-1\n
{"v":1,"kdf":"PBKDF2-SHA256","iteraciones":600000,"sal":"<b64 16 B>","iv":"<b64 12 B>","compresion":"gzip","creado":"<ISO>"}\n
<AES-256-GCM( gzip(base SQLite) ), con las dos líneas anteriores como datos asociados>
```

- Llave = PBKDF2-SHA256(clave normalizada NFC, sal, 600.000) → 256 bits. La sal cambia solo al cambiar la clave.
- IV aleatorio nuevo en cada guardado. Alterar la cabecera hace fallar el descifrado.
- La clave nunca se guarda; la llave vive solo en memoria mientras la app está abierta.
- Bloqueo automático tras 10 min en segundo plano, y botón «Bloquear ahora» (panel Seguridad).
