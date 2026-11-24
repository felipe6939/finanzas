class ErrorValidacion(Exception):
    """Uno o más datos no son válidos. `errores` es una lista de mensajes para el usuario."""

    def __init__(self, errores: list[str] | str):
        self.errores = [errores] if isinstance(errores, str) else list(errores)
        super().__init__("; ".join(self.errores))
