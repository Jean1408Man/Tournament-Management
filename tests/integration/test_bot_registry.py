"""Registro de bots nuevos con PostgreSQL y archivos reales, sin catálogo fijo."""

import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from psycopg.conninfo import conninfo_to_dict

from tournament_management.adapters.artifact_store import FileArtifactStore
from tournament_management.adapters.postgres_store import PostgresUnitOfWork
from tournament_management.application.competitor_service import CompetitorService
from tournament_management.application.ports import ArtifactIntegrityError

SOURCE = b"# Bot nuevo subido sin reiniciar\nprint('hello')\n"


def service(database, directory, max_bytes=1048576):
    return CompetitorService(
        lambda: PostgresUnitOfWork(database),
        FileArtifactStore(directory, max_bytes=max_bytes),
        max_bytes=max_bytes,
    )


def register(registry, content=SOURCE, **kwargs):
    return registry.register(
        io.BytesIO(content),
        filename=kwargs.pop("filename", "new-bot.py"),
        name=kwargs.pop("name", "Mi bot"),
        idempotency_key=kwargs.pop("idempotency_key", "key1"),
        **kwargs,
    )


def count(database, table):
    import psycopg

    with psycopg.connect(database) as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_register_restart_retrieves_exact_bytes_and_metadata(database, tmp_path):
    registry = service(database, tmp_path)
    registered = register(registry)
    assert registered.artifact.artifact_hash == hashlib.sha256(SOURCE).hexdigest()
    assert registered.artifact.protocol_version == 1
    assert registered.artifact.runtime == "python3.12"
    restarted = service(database, tmp_path)
    assert restarted.get(registered.competitor_id) == registered
    assert restarted.content(registered.competitor_id) == SOURCE
    assert register(restarted) == registered
    assert count(database, "competitors") == 1
    assert count(database, "artifacts") == 1
    assert count(database, "idempotency_keys") == 1


@pytest.mark.parametrize(
    "changes,content",
    [
        ({"filename": "bot.txt"}, SOURCE),
        ({"runtime": "bash"}, SOURCE),
        ({"protocol_version": 2}, SOURCE),
        ({"protocol_version": True}, SOURCE),
        ({"protocol": "unknown"}, SOURCE),
        ({"name": ""}, SOURCE),
        ({"name": "x" * 201}, SOURCE),
        ({"competitor_id": ""}, SOURCE),
        ({}, b""),
        ({}, b"def broken("),
        ({}, b"\xff\xfe"),
        ({}, b"x\x00"),
    ],
)
def test_invalid_upload_has_no_registered_artifact(database, tmp_path, changes, content):
    with pytest.raises(ValueError):
        register(service(database, tmp_path), content, **changes)
    assert count(database, "artifacts") == 0
    assert count(database, "competitors") == 0
    assert list(tmp_path.iterdir()) == []


def test_oversize_and_interrupted_input_never_publish(database, tmp_path):
    registry = service(database, tmp_path, max_bytes=10)
    with pytest.raises(ValueError, match="bytes"):
        register(registry, b"#" * 11)
    stream = Mock()
    stream.read.side_effect = [b"# part", OSError("upload interrupted")]
    with pytest.raises(OSError, match="interrupted"):
        registry.register(stream, filename="a.py", name="Bot", idempotency_key="k")
    assert count(database, "artifacts") == 0
    assert list(tmp_path.iterdir()) == []


def test_source_stream_can_deliver_short_reads(database, tmp_path):
    stream = Mock()
    stream.read.side_effect = [b"#", b" first", b"\n", b""]
    registry = service(database, tmp_path)
    registered = registry.register(stream, filename="a.py", name="Bot", idempotency_key="k")
    assert registry.content(registered.competitor_id) == b"# first\n"


@pytest.mark.parametrize("failure", ["publish", "commit"])
def test_publication_or_commit_failure_never_announces_partial_object(
    database,
    tmp_path,
    monkeypatch,
    failure,
):
    registry = service(database, tmp_path)
    if failure == "publish":
        monkeypatch.setattr(os, "link", Mock(side_effect=OSError("injected publication")))
    else:
        monkeypatch.setattr(
            PostgresUnitOfWork, "commit", Mock(side_effect=OSError("injected commit"))
        )
    with pytest.raises(OSError, match="injected"):
        register(registry)
    assert count(database, "artifacts") == 0
    assert count(database, "competitors") == 0
    assert count(database, "idempotency_keys") == 0
    # SQL puede fallar después de publicar un objeto completo. Queda huérfano,
    # sin registro anunciado; borrarlo aquí podría romper otra inscripción.
    if failure == "commit":
        assert FileArtifactStore(tmp_path).get(hashlib.sha256(SOURCE).hexdigest()) == SOURCE
    assert not list(tmp_path.glob(".upload-*"))


def test_corrupted_file_is_detected_after_registration(database, tmp_path):
    registry = service(database, tmp_path)
    registered = register(registry)
    path = tmp_path / f"{registered.artifact.artifact_hash}.py"
    path.chmod(0o600)
    path.write_bytes(b"# corrupted\n")
    with pytest.raises(ArtifactIntegrityError):
        registry.get(registered.competitor_id)
    with pytest.raises(ArtifactIntegrityError):
        registry.content(registered.competitor_id)
    with pytest.raises(ArtifactIntegrityError):
        register(registry)


def test_metadata_only_legacy_bot_is_not_available(database, tmp_path):
    digest = hashlib.sha256(SOURCE).hexdigest()
    with PostgresUnitOfWork(database) as work:
        work.registry.add_artifact(digest, len(SOURCE))
        work.registry.add_competitor("legacy", "Mi bot", digest)
        work.commit()
    registry = service(database, tmp_path)
    with pytest.raises(KeyError):
        registry.get("legacy")
    registered = register(registry, competitor_id="legacy")
    assert registry.content(registered.competitor_id) == SOURCE


def test_same_bytes_deduplicate_and_conflicting_identity_does_not_replace(database, tmp_path):
    registry = service(database, tmp_path)
    first = register(registry, competitor_id="c1")
    second = register(registry, competitor_id="c2", idempotency_key="key2")
    assert first.artifact == second.artifact
    assert count(database, "artifacts") == 1
    assert count(database, "competitors") == 2
    with pytest.raises(ValueError):
        register(registry, b"# different\n", competitor_id="c1", idempotency_key="new-key")
    with pytest.raises(ValueError, match="idempotencia"):
        register(registry, name="different name")
    assert registry.get("c1") == first
    assert count(database, "idempotency_keys") == 2


def test_concurrent_idempotent_registration_shares_one_identity(database, tmp_path):
    registry = service(database, tmp_path)
    barrier = Barrier(2)

    def upload():
        barrier.wait(timeout=10)
        return register(registry)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(upload) for _ in range(2)]
        first, second = [future.result(timeout=15) for future in futures]
    assert first == second
    assert count(database, "competitors") == 1
    assert count(database, "artifacts") == 1


def test_register_does_not_execute_uploaded_code(database, tmp_path):
    marker = tmp_path / "executed"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n".encode()
    register(service(database, tmp_path / "objects"), source)
    assert not marker.exists()


def test_cli_register_and_inspect_after_new_process(database, tmp_path):
    # Pasar los parámetros de libpq por entorno conserva el esquema aislado y
    # evita necesitar credenciales de desarrollo o exponerlas en los argumentos.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("TM_", "PG"))}
    options = conninfo_to_dict(database)
    env.update({"PG" + key.upper(): value for key, value in options.items()})
    env["PGDATABASE"] = options["dbname"]
    env.pop("PGDBNAME", None)
    env.update(TM_DATABASE_URL="postgresql:///", TM_DATA_DIR=str(tmp_path / "state"))
    source = tmp_path / "new.py"
    source.write_bytes(SOURCE)

    def cli(*args):
        process = subprocess.run(
            [sys.executable, "-m", "tournament_management", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert process.returncode == 0, process.stderr + process.stdout
        return json.loads(process.stdout)

    registered = cli(
        "register-bot",
        "--file",
        str(source),
        "--name",
        "Nuevo bot",
        "--idempotency-key",
        "upload-1",
    )
    assert registered == cli("inspect-bot", "--competitor-id", registered["competitor_id"])
    verified = cli("verify-artifact", "--artifact-hash", registered["artifact"]["artifact_hash"])
    assert verified["size_bytes"] == len(SOURCE)


def test_different_uploads_cannot_race_to_replace_one_competitor(database, tmp_path, monkeypatch):
    from tournament_management.adapters.postgres_registry import _PostgresRegistry

    # Forzar que ambas transacciones lean ausencia antes de intentar insertar
    # la misma identidad, con hashes distintos y por tanto sin bloqueo compartido
    # previo sobre artifacts. La restricción decide un único ganador.
    barrier = Barrier(2)
    original = _PostgresRegistry.find_competitor

    def find(self, competitor_id):
        found = original(self, competitor_id)
        barrier.wait(timeout=10)
        return found

    monkeypatch.setattr(_PostgresRegistry, "find_competitor", find)
    registry = service(database, tmp_path)

    def upload(index):
        try:
            return register(
                registry,
                f"# variant {index}\n".encode(),
                competitor_id="shared",
                idempotency_key=f"upload-{index}",
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(upload, range(2)))
    assert sum(result is not None for result in results) == 1
    assert count(database, "competitors") == 1
    assert count(database, "artifacts") == 1
    assert count(database, "idempotency_keys") == 1
