"""PostgreSQL real aislado: esquema por prueba y, si hace falta, servidor temporal.

TM_TEST_DATABASE_URL debe apuntar a una BD de pruebas con permiso CREATE SCHEMA.
Sin esa variable se busca una instalación local; nunca se usa TM_DATABASE_URL
ni se modifican los datos de desarrollo. El clúster temporal solo admite un
socket Unix privado, sin puerto TCP ni credenciales de producción.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from tournament_management.adapters.postgres_connection import migrate


@pytest.fixture(scope="session")
def postgres_server():
    configured = os.environ.get("TM_TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    executable = shutil.which("pg_config")
    if executable:
        bindir = Path(subprocess.check_output([executable, "--bindir"], text=True).strip())
    else:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        if not candidates:
            pytest.skip("Instalar PostgreSQL o definir TM_TEST_DATABASE_URL")
        bindir = candidates[-1].parent
    if os.geteuid() == 0:
        pytest.skip("initdb requiere usuario sin privilegios; definir TM_TEST_DATABASE_URL")
    with tempfile.TemporaryDirectory(prefix="tm-pg-") as directory:
        root = Path(directory)
        subprocess.run(
            [
                str(bindir / "initdb"),
                "-D",
                str(root / "data"),
                "-A",
                "trust",
                "-U",
                "tm_test",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                str(bindir / "pg_ctl"),
                "-D",
                str(root / "data"),
                "-l",
                str(root / "server.log"),
                "-o",
                f"-c listen_addresses='' -c unix_socket_directories='{root}'",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            yield make_conninfo(dbname="postgres", user="tm_test", host=str(root))
        finally:
            subprocess.run(
                [str(bindir / "pg_ctl"), "-D", str(root / "data"), "-m", "fast", "-w", "stop"],
                check=True,
                capture_output=True,
                text=True,
            )


@pytest.fixture
def empty_database(postgres_server):
    schema = "test_" + uuid4().hex
    with psycopg.connect(postgres_server, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema))
        )
    try:
        yield make_conninfo(postgres_server, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(postgres_server, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema))
            )


@pytest.fixture
def database(empty_database):
    migrate(empty_database)
    return empty_database
