"""Errores de aplicación que las interfaces traducen a su propio protocolo."""


class ConflictError(ValueError):
    """Una clave o identidad existente describe otro contenido inmutable."""
