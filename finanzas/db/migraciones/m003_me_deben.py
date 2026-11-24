"""«Me deben»: dinero prestado a otras personas (cuentas por cobrar).

- prestamos: cada deuda a favor del usuario. fecha y cuenta_id NULL = la deuda ya
  existía en el punto de partida (no sale de ninguna cuenta del sistema).
- abonos_prestamo: pagos recibidos. Entran a una cuenta y reducen lo que te deben.
  No son ingresos: el dinero ya era tuyo.

Un préstamo no es gasto ni un abono es ingreso: así no se distorsionan los
indicadores del mes. Ninguna de las dos tablas admite borrado físico.
"""

_FECHA = "GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'"

ESQUEMA = f"""
CREATE TABLE prestamos (
    id           INTEGER PRIMARY KEY,
    persona      TEXT NOT NULL CHECK (length(trim(persona)) > 0),
    valor        INTEGER NOT NULL CHECK (valor > 0),
    fecha        TEXT CHECK (fecha IS NULL OR fecha {_FECHA}),
    cuenta_id    INTEGER REFERENCES cuentas(id),
    nota         TEXT,
    creado_en    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    eliminado_en TEXT,
    CHECK ((fecha IS NULL) = (cuenta_id IS NULL))
);

CREATE TABLE abonos_prestamo (
    id           INTEGER PRIMARY KEY,
    prestamo_id  INTEGER NOT NULL REFERENCES prestamos(id),
    fecha        TEXT NOT NULL CHECK (fecha {_FECHA}),
    valor        INTEGER NOT NULL CHECK (valor > 0),
    cuenta_id    INTEGER NOT NULL REFERENCES cuentas(id),
    nota         TEXT,
    creado_en    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    eliminado_en TEXT
);
CREATE INDEX ix_abonos_prestamo ON abonos_prestamo(prestamo_id);
"""

_COLUMNAS = {
    "prestamos": ["persona", "valor", "fecha", "cuenta_id", "nota", "eliminado_en"],
    "abonos_prestamo": ["prestamo_id", "fecha", "valor", "cuenta_id", "nota", "eliminado_en"],
}


def _json(tabla: str, alias: str) -> str:
    return "json_object(" + ", ".join(f"'{c}', {alias}.{c}" for c in _COLUMNAS[tabla]) + ")"


def _triggers() -> str:
    sql = []
    for tabla in _COLUMNAS:
        sql.append(f"""
CREATE TRIGGER tr_{tabla}_no_borrar
BEFORE DELETE ON {tabla}
WHEN COALESCE((SELECT valor FROM configuracion WHERE clave = 'permitir_borrado_fisico'), '0') <> '1'
BEGIN
    SELECT RAISE(ABORT, 'No se borra fisicamente: use la eliminacion logica.');
END;

CREATE TRIGGER tr_audit_{tabla}_crear AFTER INSERT ON {tabla}
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, despues)
    VALUES ('{tabla}', NEW.id, 'crear', {_json(tabla, 'NEW')});
END;

CREATE TRIGGER tr_audit_{tabla}_modificar AFTER UPDATE ON {tabla}
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, antes, despues)
    VALUES ('{tabla}', NEW.id,
            CASE WHEN OLD.eliminado_en IS NULL AND NEW.eliminado_en IS NOT NULL
                 THEN 'eliminar' ELSE 'modificar' END,
            {_json(tabla, 'OLD')}, {_json(tabla, 'NEW')});
END;
""")
    return "\n".join(sql)


def aplicar(con, ejecutar_script) -> None:
    ejecutar_script(con, ESQUEMA)
    ejecutar_script(con, _triggers())
