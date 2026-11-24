"""Movimientos de dinero entre cuentas propias (p. ej. retirar de Nequi a Efectivo).

No son ingresos ni gastos: el total no cambia, solo en qué cuenta está el dinero.
Tabla aparte de `movimientos` para no mezclarlos con las cifras del mes.
"""

ESQUEMA = """
CREATE TABLE transferencias (
    id                INTEGER PRIMARY KEY,
    fecha             TEXT NOT NULL CHECK (fecha GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
    valor             INTEGER NOT NULL CHECK (valor > 0),
    cuenta_origen_id  INTEGER NOT NULL REFERENCES cuentas(id),
    cuenta_destino_id INTEGER NOT NULL REFERENCES cuentas(id),
    nota              TEXT,
    creado_en         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    eliminado_en      TEXT,
    CHECK (cuenta_origen_id <> cuenta_destino_id)
);
CREATE INDEX ix_transferencias_fecha ON transferencias(fecha);

CREATE TRIGGER tr_transferencias_no_borrar
BEFORE DELETE ON transferencias
WHEN COALESCE((SELECT valor FROM configuracion WHERE clave = 'permitir_borrado_fisico'), '0') <> '1'
BEGIN
    SELECT RAISE(ABORT, 'No se borra fisicamente: use la eliminacion logica.');
END;

CREATE TRIGGER tr_audit_transferencias_crear AFTER INSERT ON transferencias
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, despues)
    VALUES ('transferencias', NEW.id, 'crear', json_object(
        'fecha', NEW.fecha, 'valor', NEW.valor, 'cuenta_origen_id', NEW.cuenta_origen_id,
        'cuenta_destino_id', NEW.cuenta_destino_id, 'nota', NEW.nota, 'eliminado_en', NEW.eliminado_en));
END;

CREATE TRIGGER tr_audit_transferencias_modificar AFTER UPDATE ON transferencias
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, antes, despues)
    VALUES ('transferencias', NEW.id,
        CASE WHEN OLD.eliminado_en IS NULL AND NEW.eliminado_en IS NOT NULL THEN 'eliminar' ELSE 'modificar' END,
        json_object('fecha', OLD.fecha, 'valor', OLD.valor, 'cuenta_origen_id', OLD.cuenta_origen_id,
                    'cuenta_destino_id', OLD.cuenta_destino_id, 'nota', OLD.nota, 'eliminado_en', OLD.eliminado_en),
        json_object('fecha', NEW.fecha, 'valor', NEW.valor, 'cuenta_origen_id', NEW.cuenta_origen_id,
                    'cuenta_destino_id', NEW.cuenta_destino_id, 'nota', NEW.nota, 'eliminado_en', NEW.eliminado_en));
END;
"""


def aplicar(con, ejecutar_script) -> None:
    ejecutar_script(con, ESQUEMA)
