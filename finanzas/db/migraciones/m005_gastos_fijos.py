"""Gastos fijos mensuales (arriendo, servicios, suscripciones...).

- gastos_fijos: la plantilla de cada gasto que se repite cada mes (valor, categoría,
  cuenta y día del mes). `automatico` = 1 registra el gasto cada mes desde `desde_mes`.
- gastos_fijos_registros: qué mes ya quedó registrado y con qué movimiento. Garantiza
  que un mes nunca se registre dos veces (UNIQUE). Si el usuario elimina el movimiento
  generado, el registro se conserva y ese mes no se vuelve a crear.

Se incluye un ejemplo inactivo («Arriendo» $ 550.000, pedido por el usuario): no afecta
ninguna cifra hasta que se marque la casilla de registro automático.
"""

_MES = "GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'"

ESQUEMA = f"""
CREATE TABLE gastos_fijos (
    id           INTEGER PRIMARY KEY,
    descripcion  TEXT NOT NULL CHECK (length(trim(descripcion)) > 0),
    valor        INTEGER NOT NULL CHECK (valor > 0),
    categoria_id INTEGER NOT NULL REFERENCES categorias(id),
    cuenta_id    INTEGER NOT NULL REFERENCES cuentas(id),
    dia          INTEGER NOT NULL CHECK (dia BETWEEN 1 AND 31),
    automatico   INTEGER NOT NULL DEFAULT 0 CHECK (automatico IN (0, 1)),
    desde_mes    TEXT CHECK (desde_mes IS NULL OR desde_mes {_MES}),
    creado_en    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    eliminado_en TEXT,
    CHECK (automatico = 0 OR desde_mes IS NOT NULL)
);

CREATE TABLE gastos_fijos_registros (
    id            INTEGER PRIMARY KEY,
    gasto_fijo_id INTEGER NOT NULL REFERENCES gastos_fijos(id),
    mes           TEXT NOT NULL CHECK (mes {_MES}),
    movimiento_id INTEGER REFERENCES movimientos(id) ON DELETE CASCADE,
    creado_en     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    UNIQUE (gasto_fijo_id, mes)
);

CREATE TRIGGER tr_gastos_fijos_no_borrar
BEFORE DELETE ON gastos_fijos
WHEN COALESCE((SELECT valor FROM configuracion WHERE clave = 'permitir_borrado_fisico'), '0') <> '1'
BEGIN
    SELECT RAISE(ABORT, 'No se borra fisicamente: use la eliminacion logica.');
END;
"""

_COLUMNAS = ["descripcion", "valor", "categoria_id", "cuenta_id", "dia", "automatico", "desde_mes",
             "eliminado_en"]


def _json(alias: str) -> str:
    return "json_object(" + ", ".join(f"'{c}', {alias}.{c}" for c in _COLUMNAS) + ")"


AUDITORIA = f"""
CREATE TRIGGER tr_audit_gastos_fijos_crear AFTER INSERT ON gastos_fijos
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, despues)
    VALUES ('gastos_fijos', NEW.id, 'crear', {_json('NEW')});
END;

CREATE TRIGGER tr_audit_gastos_fijos_modificar AFTER UPDATE ON gastos_fijos
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, antes, despues)
    VALUES ('gastos_fijos', NEW.id,
        CASE WHEN OLD.eliminado_en IS NULL AND NEW.eliminado_en IS NOT NULL THEN 'eliminar' ELSE 'modificar' END,
        {_json('OLD')}, {_json('NEW')});
END;
"""


def _ejemplo_arriendo(con) -> None:
    cat = con.execute(
        "SELECT c.id FROM categorias c JOIN categorias p ON p.id = c.padre_id "
        "WHERE c.tipo = 'gasto' AND c.nombre = 'Arriendo' AND p.nombre = 'Vivienda' AND c.activa = 1"
    ).fetchone()
    cuenta = con.execute(
        "SELECT id FROM cuentas WHERE activa = 1 ORDER BY por_defecto DESC, orden, id LIMIT 1"
    ).fetchone()
    if cat and cuenta:
        con.execute(
            "INSERT INTO gastos_fijos (descripcion, valor, categoria_id, cuenta_id, dia) VALUES (?, ?, ?, ?, ?)",
            ("Arriendo", 550_000, cat[0], cuenta[0], 1),
        )


def aplicar(con, ejecutar_script) -> None:
    ejecutar_script(con, ESQUEMA)
    ejecutar_script(con, AUDITORIA)
    _ejemplo_arriendo(con)
