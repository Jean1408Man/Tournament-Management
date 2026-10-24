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
        raise OSError("PostgreSQL no disponible") from exc


def check_schema(database_url: str) -> None:
    """No declarar listo un coordinador conectado a una base aún sin migrar."""
    psycopg, _ = driver()
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
            versions = {row[0] for row in rows}
            if not {"0001_initial", "0002_durable", "0003_artifact_availability"} <= versions:
                raise OSError("El esquema PostgreSQL requiere tm migrate")
    except psycopg.Error as exc:
        raise OSError("El esquema PostgreSQL no está disponible; ejecuta tm migrate") from exc


def migrate(database_url: str) -> None:
    """Migra una sola vez por versión, en una transacción y sin carreras de DDL.

    El advisory lock serializa dos coordinadores que arrancan simultáneamente.
    Una migración fallida revierte también su marca; nunca se anuncia aplicada
    antes de que PostgreSQL haya confirmado todos sus cambios.
    """
    psycopg, _ = driver()
    resources = files("tournament_management.adapters")
    migrations = (
        ("0001_initial", "postgres_schema.sql"),
        ("0002_durable", "postgres_0002_durable.sql"),
        ("0003_artifact_availability", "postgres_0003_artifact_availability.sql"),
    )
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(7348203101)")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                for version, filename in migrations:
                    cursor.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
                    if cursor.fetchone() is not None:
                        continue
                    cursor.execute(resources.joinpath(filename).read_text())
                    cursor.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s) "
                        "ON CONFLICT DO NOTHING",
                        (version,),
                    )
    except psycopg.Error as exc:
        raise OSError("No se pudo migrar PostgreSQL") from exc
