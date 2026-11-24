"""Esquema inicial.

Decisiones clave (ver docs/arquitectura.md):
- Valores en pesos enteros (INTEGER), siempre positivos; el tipo define el sentido.
- Tipos de movimiento: ingreso, gasto, ahorro. 'transferencia' queda reservado
  en el esquema para cuando se quiera llevar el saldo exacto de cada cuenta.
- Los movimientos nunca se borran físicamente (borrado lógico con eliminado_en),
  salvo los datos de ejemplo (origen = 'demo') o un reinicio explícito.
- Toda creación, modificación o eliminación queda en la tabla auditoria.
"""

ESQUEMA = """
CREATE TABLE configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE cuentas (
    id            INTEGER PRIMARY KEY,
    nombre        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    tipo          TEXT NOT NULL CHECK (tipo IN ('banco', 'billetera', 'efectivo')),
    alias         TEXT NOT NULL DEFAULT '',
    saldo_inicial INTEGER NOT NULL DEFAULT 0 CHECK (saldo_inicial >= 0),
    por_defecto   INTEGER NOT NULL DEFAULT 0 CHECK (por_defecto IN (0, 1)),
    activa        INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    orden         INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_cuenta_por_defecto ON cuentas(por_defecto) WHERE por_defecto = 1;

CREATE TABLE categorias (
    id         INTEGER PRIMARY KEY,
    nombre     TEXT NOT NULL,
    padre_id   INTEGER REFERENCES categorias(id),
    tipo       TEXT NOT NULL CHECK (tipo IN ('gasto', 'ingreso')),
    naturaleza TEXT CHECK (naturaleza IN ('fijo', 'variable', 'extraordinario')),
    necesidad  TEXT CHECK (necesidad IN ('esencial', 'discrecional')),
    activa     INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    orden      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ux_categoria ON categorias(tipo, COALESCE(padre_id, 0), nombre COLLATE NOCASE);

CREATE TABLE metas_ahorro (
    id             INTEGER PRIMARY KEY,
    nombre         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    monto_objetivo INTEGER CHECK (monto_objetivo IS NULL OR monto_objetivo > 0),
    fecha_objetivo TEXT,
    activa         INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    creado_en      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);

CREATE TABLE reglas_clasificacion (
    id           INTEGER PRIMARY KEY,
    patron       TEXT NOT NULL,
    tipo         TEXT NOT NULL CHECK (tipo IN ('gasto', 'ingreso')),
    categoria_id INTEGER NOT NULL REFERENCES categorias(id),
    origen       TEXT NOT NULL DEFAULT 'base' CHECK (origen IN ('base', 'usuario')),
    creado_en    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
    UNIQUE (patron, tipo)
);

CREATE TABLE lotes_importacion (
    id              INTEGER PRIMARY KEY,
    origen          TEXT NOT NULL CHECK (origen IN ('archivo', 'texto')),
    archivo_nombre  TEXT,
    archivo_hash    TEXT NOT NULL,
    ruta_copia      TEXT NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'borrador'
                    CHECK (estado IN ('borrador', 'confirmado', 'revertido', 'descartado')),
    creado_en       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
    confirmado_en   TEXT,
    revertido_en    TEXT
);

CREATE TABLE movimientos (
    id                INTEGER PRIMARY KEY,
    tipo              TEXT NOT NULL CHECK (tipo IN ('ingreso', 'gasto', 'ahorro', 'transferencia')),
    fecha             TEXT NOT NULL CHECK (fecha GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    valor             INTEGER NOT NULL CHECK (valor > 0),
    descripcion       TEXT NOT NULL CHECK (length(trim(descripcion)) > 0),
    cuenta_id         INTEGER NOT NULL REFERENCES cuentas(id),
    cuenta_destino_id INTEGER REFERENCES cuentas(id),
    categoria_id      INTEGER REFERENCES categorias(id),
    meta_id           INTEGER REFERENCES metas_ahorro(id),
    naturaleza        TEXT CHECK (naturaleza IN ('fijo', 'variable', 'extraordinario')),
    necesidad         TEXT CHECK (necesidad IN ('esencial', 'discrecional')),
    observaciones     TEXT NOT NULL DEFAULT '',
    origen            TEXT NOT NULL CHECK (origen IN ('manual', 'importacion', 'demo')),
    lote_id           INTEGER REFERENCES lotes_importacion(id),
    huella            TEXT,
    creado_en         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
    actualizado_en    TEXT,
    eliminado_en      TEXT,
    CHECK (tipo NOT IN ('ingreso', 'gasto') OR categoria_id IS NOT NULL),
    CHECK (tipo <> 'transferencia' OR cuenta_destino_id IS NOT NULL),
    CHECK (origen <> 'importacion' OR (lote_id IS NOT NULL AND huella IS NOT NULL))
);
CREATE UNIQUE INDEX ux_movimiento_huella ON movimientos(huella)
    WHERE huella IS NOT NULL AND eliminado_en IS NULL;
CREATE INDEX ix_movimiento_fecha ON movimientos(fecha) WHERE eliminado_en IS NULL;
CREATE INDEX ix_movimiento_lote ON movimientos(lote_id);

CREATE TABLE lineas_importacion (
    id                  INTEGER PRIMARY KEY,
    lote_id             INTEGER NOT NULL REFERENCES lotes_importacion(id),
    num_linea           INTEGER NOT NULL,
    texto               TEXT NOT NULL,
    fecha_contexto      TEXT,
    huella              TEXT NOT NULL,
    estado              TEXT NOT NULL CHECK (estado IN
                        ('lista', 'revisar', 'ya_importada', 'ignorada', 'descartada', 'importada')),
    tipo                TEXT CHECK (tipo IN ('ingreso', 'gasto', 'ahorro')),
    fecha               TEXT,
    valor               INTEGER,
    descripcion         TEXT,
    cuenta_id           INTEGER REFERENCES cuentas(id),
    categoria_id        INTEGER REFERENCES categorias(id),
    meta_id             INTEGER REFERENCES metas_ahorro(id),
    observaciones       TEXT NOT NULL DEFAULT '',
    origen_campos       TEXT NOT NULL DEFAULT '{}',
    notas               TEXT NOT NULL DEFAULT '[]',
    problemas           TEXT NOT NULL DEFAULT '[]',
    advertencias_ok     INTEGER NOT NULL DEFAULT 0,
    descartada_usuario  INTEGER NOT NULL DEFAULT 0,
    recordar_patron     TEXT,
    movimiento_id       INTEGER REFERENCES movimientos(id),
    UNIQUE (lote_id, num_linea)
);

CREATE TABLE auditoria (
    id          INTEGER PRIMARY KEY,
    tabla       TEXT NOT NULL,
    registro_id INTEGER NOT NULL,
    accion      TEXT NOT NULL CHECK (accion IN ('crear', 'modificar', 'eliminar')),
    antes       TEXT,
    despues     TEXT,
    en          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
CREATE INDEX ix_auditoria_registro ON auditoria(tabla, registro_id);

-- Protección: un movimiento real no se puede borrar físicamente.
CREATE TRIGGER tr_movimientos_no_borrar
BEFORE DELETE ON movimientos
WHEN OLD.origen <> 'demo'
     AND COALESCE((SELECT valor FROM configuracion WHERE clave = 'permitir_borrado_fisico'), '0') <> '1'
BEGIN
    SELECT RAISE(ABORT, 'Los movimientos no se borran fisicamente: use la eliminacion logica.');
END;
"""

_COLUMNAS_AUDITADAS = {
    "movimientos": [
        "tipo", "fecha", "valor", "descripcion", "cuenta_id", "cuenta_destino_id",
        "categoria_id", "meta_id", "naturaleza", "necesidad", "observaciones",
        "origen", "lote_id", "eliminado_en",
    ],
    "cuentas": ["nombre", "tipo", "alias", "saldo_inicial", "por_defecto", "activa"],
    "categorias": ["nombre", "padre_id", "tipo", "naturaleza", "necesidad", "activa"],
    "metas_ahorro": ["nombre", "monto_objetivo", "fecha_objetivo", "activa"],
    "configuracion": ["clave", "valor"],
}


def _json(tabla: str, alias: str) -> str:
    pares = ", ".join(f"'{c}', {alias}.{c}" for c in _COLUMNAS_AUDITADAS[tabla])
    return f"json_object({pares})"


def _triggers_auditoria() -> str:
    sql = []
    for tabla in _COLUMNAS_AUDITADAS:
        rid = "NEW.rowid" if tabla == "configuracion" else "NEW.id"
        rid_old = "OLD.rowid" if tabla == "configuracion" else "OLD.id"
        accion_update = (
            "CASE WHEN OLD.eliminado_en IS NULL AND NEW.eliminado_en IS NOT NULL "
            "THEN 'eliminar' ELSE 'modificar' END"
            if tabla == "movimientos" else "'modificar'"
        )
        # Los datos de ejemplo borrados no dejan rastro en la auditoría.
        cuando_borrar = "WHEN OLD.origen <> 'demo'" if tabla == "movimientos" else ""
        sql.append(f"""
CREATE TRIGGER tr_audit_{tabla}_crear AFTER INSERT ON {tabla}
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, despues)
    VALUES ('{tabla}', {rid}, 'crear', {_json(tabla, 'NEW')});
END;

CREATE TRIGGER tr_audit_{tabla}_modificar AFTER UPDATE ON {tabla}
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, antes, despues)
    VALUES ('{tabla}', {rid}, {accion_update}, {_json(tabla, 'OLD')}, {_json(tabla, 'NEW')});
END;

CREATE TRIGGER tr_audit_{tabla}_borrar AFTER DELETE ON {tabla}
{cuando_borrar}
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, antes)
    VALUES ('{tabla}', {rid_old}, 'eliminar', {_json(tabla, 'OLD')});
END;
""")
    return "\n".join(sql)


def aplicar(con, ejecutar_script) -> None:
    ejecutar_script(con, ESQUEMA)
    ejecutar_script(con, _triggers_auditoria())
