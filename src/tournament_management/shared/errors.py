"""Errores de aplicación que las interfaces traducen a su propio protocolo."""


class ConflictError(ValueError):
    """Una clave o identidad existente describe otro contenido inmutable."""


class OverloadedError(Exception):
    """Límite de admisión alcanzado (5.1, E1.6): no se guardó nada; reintentar luego."""
