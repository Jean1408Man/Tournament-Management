"""Conexión, comprobación de disponibilidad y esquema PostgreSQL."""

from importlib.resources import files
from typing import Any


def driver() -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:  # pragma: no cover - depende de la instalación
        raise OSError("El controlador PostgreSQL no está instalado") from exc
    return psycopg, dict_row


def check_connection(database_url: str) -> None:
    """Falla temprano si PostgreSQL no está disponible o las credenciales no sirven."""
    psycopg, _ = driver()
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
    except psycopg.Error as exc:
        raise OSError(f"PostgreSQL no disponible: {exc}") from exc


def migrate(database_url: str) -> None:
    """Aplica el esquema idempotente incluido con el paquete."""
    psycopg, _ = driver()
    schema = files("tournament_management.adapters").joinpath("postgres_schema.sql").read_text()
    try:
        with psycopg.connect(database_url, connect_timeout=5, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(schema)
    except psycopg.Error as exc:
        raise OSError(f"No se pudo migrar PostgreSQL: {exc}") from exc
