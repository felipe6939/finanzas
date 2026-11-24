"""Datos base: cuentas, categorías y reglas de clasificación iniciales.

Todo es editable desde Configuración. Las reglas solo PROPONEN una categoría
durante la importación; el usuario la ve antes de confirmar.
"""

FECHA_INICIO = "2026-11-16"

# (nombre, tipo, alias, por_defecto)
CUENTAS = [
    ("Nequi", "billetera", "nequi", 1),
    ("Bancolombia", "banco", "bancolombia,bancol", 0),
    ("BBVA", "banco", "bbva", 0),
    ("Efectivo", "efectivo", "efectivo,cash", 0),
]

F, V, X = "fijo", "variable", "extraordinario"
E, D = "esencial", "discrecional"

# categoría: (naturaleza, necesidad, [(subcategoría, naturaleza, necesidad, [patrones])])
GASTOS = {
    "Vivienda": (F, E, [
        ("Arriendo", F, E, ["arriendo", "arrendamiento"]),
        ("Servicios públicos", F, E, ["luz", "agua", "energia", "acueducto", "gas natural",
                                      "recibo de luz", "recibo del agua", "epm", "enel", "codensa", "vanti"]),
        ("Internet y telefonía", F, E, ["internet", "plan celular", "recarga", "claro", "movistar", "tigo", "wom"]),
        ("Administración", F, E, ["administracion"]),
        ("Hogar y mantenimiento", V, E, ["ferreteria", "aseo", "plomero", "reparacion casa"]),
    ]),
    "Alimentación": (V, E, [
        ("Mercado", V, E, ["mercado", "supermercado", "d1", "ara", "exito", "carulla", "olimpica",
                           "jumbo", "makro", "isimo", "fruver", "tienda"]),
        ("Restaurantes y almuerzos", V, E, ["almuerzo", "corrientazo", "desayuno", "cena", "comida",
                                            "restaurante"]),
        ("Domicilios", V, D, ["rappi", "domicilio", "didi food", "ifood"]),
        ("Café y snacks", V, D, ["cafe", "tinto", "snack", "onces", "mecato", "empanada", "pan", "panaderia",
                                 "helado", "gaseosa"]),
    ]),
    "Transporte": (V, E, [
        ("Gasolina", V, E, ["gasolina", "tanqueo", "tanquear", "acpm"]),
        ("Transporte público", V, E, ["bus", "buseta", "transmilenio", "sitp", "metro", "mio", "megabus",
                                      "metroplus", "pasaje", "tarjeta tullave", "recarga tullave"]),
        ("Taxi y apps", V, D, ["taxi", "uber", "didi", "cabify", "indriver", "picap"]),
        ("Parqueadero y peajes", V, E, ["parqueadero", "peaje", "parqueo"]),
        ("Mantenimiento vehículo", X, E, ["taller", "mecanico", "lavada carro", "lavada moto",
                                          "cambio de aceite", "llantas", "repuesto"]),
        ("SOAT e impuestos vehículo", X, E, ["soat", "tecnomecanica", "impuesto vehicular"]),
    ]),
    "Salud": (V, E, [
        ("Medicamentos", V, E, ["farmacia", "drogueria", "drogas", "medicamento", "pastillas"]),
        ("Consultas y exámenes", X, E, ["consulta", "medico", "odontologo", "examen", "laboratorio",
                                        "copago", "cita"]),
        ("Seguros y prepagada", F, E, ["prepagada", "seguro de vida", "poliza"]),
        ("Deporte y bienestar", F, D, ["gimnasio", "gym", "smartfit", "bodytech"]),
    ]),
    "Cuidado personal": (V, D, [
        ("Ropa y calzado", V, D, ["ropa", "zapatos", "tenis", "camisa", "pantalon"]),
        ("Peluquería y aseo personal", V, D, ["peluqueria", "barberia", "corte de pelo", "shampoo",
                                              "desodorante"]),
    ]),
    "Entretenimiento": (V, D, [
        ("Salidas", V, D, ["cine", "bar", "cerveza", "cervezas", "fiesta", "salida", "rumba", "concierto"]),
        ("Suscripciones", F, D, ["netflix", "spotify", "disney", "youtube premium", "prime video",
                                 "amazon prime", "hbo", "max", "icloud", "google one", "chatgpt", "claude"]),
        ("Viajes", X, D, ["viaje", "hotel", "airbnb", "tiquete", "vuelo"]),
        ("Hobbies y compras", V, D, ["libro", "juego", "videojuego", "amazon", "mercadolibre", "temu", "shein"]),
    ]),
    "Educación": (V, E, [
        ("Cursos y formación", X, E, ["curso", "matricula", "semestre", "universidad", "platzi", "udemy"]),
        ("Materiales", V, E, ["papeleria", "fotocopias", "cuaderno"]),
    ]),
    "Financieros": (V, E, [
        ("4x1000 (GMF)", V, E, ["4x1000", "gmf", "cuatro por mil"]),
        ("Comisiones bancarias", V, E, ["cuota de manejo", "comision", "comision bancaria"]),
        ("Intereses", V, E, ["intereses", "interes"]),
    ]),
    "Familia y regalos": (V, D, [
        ("Regalos", X, D, ["regalo", "cumpleanos", "detalle"]),
        ("Apoyo familiar", F, E, ["mesada", "ayuda familiar", "giro familia"]),
        ("Donaciones", X, D, ["donacion", "limosna", "diezmo"]),
    ]),
    "Mascotas": (V, E, [
        ("Alimento y cuidado", V, E, ["concentrado", "veterinario", "veterinaria", "purina"]),
    ]),
    "Otros gastos": (V, D, [
        ("Imprevistos", X, E, ["imprevisto"]),
        ("Varios", V, D, []),
    ]),
}

INGRESOS = {
    "Salario": (F, None, [
        ("Salario", F, None, ["salario", "sueldo", "nomina", "pago quincena", "quincena"]),
        ("Horas extras", V, None, ["horas extras", "hora extra", "extras"]),
        ("Prima de servicios", F, None, ["prima", "prima de servicios"]),
        ("Bonificaciones", X, None, ["bono", "bonificacion", "comisiones"]),
    ]),
    "Ingresos adicionales": (V, None, [
        ("Trabajos independientes", V, None, ["freelance", "honorarios", "proyecto"]),
        ("Ventas", X, None, ["venta", "vendi"]),
        ("Intereses y rendimientos", V, None, ["rendimientos", "rendimiento"]),
    ]),
    "Otros ingresos": (X, None, [
        ("Reembolsos", X, None, ["reembolso", "devolucion", "me pagaron"]),
        ("Regalos recibidos", X, None, ["me regalaron", "regalo recibido"]),
        ("Varios", X, None, []),
    ]),
}


def aplicar(con, ejecutar_script) -> None:
    con.execute("INSERT INTO configuracion (clave, valor) VALUES ('fecha_inicio', ?)", (FECHA_INICIO,))
    con.execute("INSERT INTO configuracion (clave, valor) VALUES ('ahorro_inicial', '0')")
    con.execute("INSERT INTO configuracion (clave, valor) VALUES ('permitir_borrado_fisico', '0')")

    for orden, (nombre, tipo, alias, defecto) in enumerate(CUENTAS):
        con.execute(
            "INSERT INTO cuentas (nombre, tipo, alias, por_defecto, orden) VALUES (?, ?, ?, ?, ?)",
            (nombre, tipo, alias, defecto, orden),
        )

    for tipo, arbol in (("gasto", GASTOS), ("ingreso", INGRESOS)):
        for orden, (cat, (nat, nec, subs)) in enumerate(arbol.items()):
            padre = con.execute(
                "INSERT INTO categorias (nombre, tipo, naturaleza, necesidad, orden) VALUES (?, ?, ?, ?, ?)",
                (cat, tipo, nat, nec, orden),
            ).lastrowid
            for orden_sub, (sub, nat_s, nec_s, patrones) in enumerate(subs):
                sub_id = con.execute(
                    "INSERT INTO categorias (nombre, padre_id, tipo, naturaleza, necesidad, orden) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sub, padre, tipo, nat_s, nec_s, orden_sub),
                ).lastrowid
                for patron in patrones:
                    con.execute(
                        "INSERT OR IGNORE INTO reglas_clasificacion (patron, tipo, categoria_id, origen) "
                        "VALUES (?, ?, ?, 'base')",
                        (patron, tipo, sub_id),
                    )
