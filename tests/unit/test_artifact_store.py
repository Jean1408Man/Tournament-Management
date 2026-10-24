"""Publicación de archivos completos, integridad y ejecución por hash."""

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from tournament_management.adapters.artifact_store import FileArtifactStore
from tournament_management.adapters.registered_execution import run_registered_match
from tournament_management.application.ports import ArtifactIntegrityError
from tournament_management.domain.messages import AttemptSpec, MatchSpec, OutcomeKind

ROOT = Path(__file__).resolve().parents[2]


def test_content_addressed_store_survives_new_instance(tmp_path):
    directory = tmp_path / "state" / "artifacts"
    content = b"print('hello')\n"
    store = FileArtifactStore(directory)
    digest = store.put(content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert FileArtifactStore(directory).get(digest) == content
    assert store.put(content) == digest
    assert list(directory.iterdir()) == [directory / f"{digest}.py"]
    assert (directory / f"{digest}.py").stat().st_mode & 0o222 == 0


def test_concurrent_writers_publish_one_complete_object(tmp_path):
    store = FileArtifactStore(tmp_path)
    content = b"# bot\n" * 10000
    with ThreadPoolExecutor(max_workers=4) as executor:
        digests = list(executor.map(store.put, [content] * 8))
    assert len(set(digests)) == 1
    assert store.get(digests[0]) == content
    assert len(list(tmp_path.iterdir())) == 1


def test_pending_upload_is_not_readable_and_link_failure_cleans_temporary(tmp_path, monkeypatch):
    store = FileArtifactStore(tmp_path)
    content = b"# bot\n"
    digest = hashlib.sha256(content).hexdigest()

    def interrupted_link(source, destination):
        assert Path(source).read_bytes() == content
        with pytest.raises(ArtifactIntegrityError):
            store.get(digest)
        raise OSError("publication failed")

    monkeypatch.setattr(os, "link", interrupted_link)
    with pytest.raises(OSError, match="publication failed"):
        store.put(content)
    assert list(tmp_path.iterdir()) == []


def test_sync_failure_cannot_report_success(tmp_path, monkeypatch):
    store = FileArtifactStore(tmp_path)
    monkeypatch.setattr(store, "_sync_directory", Mock(side_effect=OSError("fsync failed")))
    with pytest.raises(OSError, match="fsync failed"):
        store.put(b"# complete but not announced\n")
    assert not list(tmp_path.glob(".upload-*"))


@pytest.mark.parametrize("mutation", ["corrupt", "symlink", "fifo", "missing"])
def test_invalid_object_is_never_returned_or_silently_replaced(tmp_path, mutation):
    store = FileArtifactStore(tmp_path)
    content = b"# original\n"
    digest = store.put(content)
    path = tmp_path / f"{digest}.py"
    path.unlink()
    if mutation == "corrupt":
        path.write_bytes(b"# tampered\n")
    elif mutation == "symlink":
        other = tmp_path / "other"
        other.write_bytes(content)
        path.symlink_to(other)
    elif mutation == "fifo":
        os.mkfifo(path)
    with pytest.raises(ArtifactIntegrityError):
        store.get(digest)
    if mutation != "missing":
        with pytest.raises(ArtifactIntegrityError):
            store.put(content)


@pytest.mark.parametrize("digest", ["../secret", "a" * 63, "A" * 64, "g" * 64])
def test_hash_cannot_be_used_as_path(tmp_path, digest):
    with pytest.raises(ValueError):
        FileArtifactStore(tmp_path).get(digest)


def test_upload_size_limit_and_read_limit(tmp_path):
    store = FileArtifactStore(tmp_path, max_bytes=4)
    for content in (b"", b"12345"):
        with pytest.raises(ValueError):
            store.put(content)
    digest = store.put(b"1234")
    with pytest.raises(ArtifactIntegrityError):
        FileArtifactStore(tmp_path, max_bytes=3).get(digest)


def attempt(hashes):
    return AttemptSpec(
        attempt_id="a1",
        generation=1,
        match=MatchSpec(
            tournament_id="t1",
            match_id="m1",
            competitor_ids=("c1", "c2"),
            artifact_hashes=hashes,
            initial_piles=(1, 2, 3),
            first_player=0,
            seed=42,
        ),
        turn_timeout_ms=1000,
        match_timeout_ms=10000,
        memory_limit_bytes=268435456,
        output_limit_bytes=1048576,
    )


def test_referee_runs_verified_bots_from_store(tmp_path):
    store = FileArtifactStore(tmp_path)
    hashes = tuple(
        store.put((ROOT / "bots" / filename).read_bytes())
        for filename in (
            "optimal.py",
            "random_bot.py",
        )
    )
    result = run_registered_match(attempt(hashes), store, worker_id="worker-test")
    assert result.kind == OutcomeKind.COMPLETED
    assert result.worker_id == "worker-test"


def test_corruption_prevents_starting_either_bot(tmp_path, monkeypatch):
    from tournament_management.adapters import registered_execution

    store = FileArtifactStore(tmp_path)
    digest = store.put(b"# valid Python\n")
    run = Mock()
    monkeypatch.setattr(registered_execution, "run_match", run)
    result = run_registered_match(attempt((digest, "f" * 64)), store)
    assert result.kind == OutcomeKind.INFRASTRUCTURE_FAILURE
    assert not result.winner_id
    run.assert_not_called()


def test_execution_uses_verified_private_copies_and_cleans_them(tmp_path, monkeypatch):
    from tournament_management.adapters import registered_execution

    store = FileArtifactStore(tmp_path)
    digest = store.put(b"# bot\n")
    paths = []
    sentinel = object()

    def run(spec, commands, **kwargs):
        for command in commands:
            path = Path(command[-1])
            assert command[1:3] == ("-I", "-B")
            assert path.parent != store.directory
            assert path.read_bytes() == b"# bot\n"
            paths.append(path)
        return sentinel

    monkeypatch.setattr(registered_execution, "run_match", run)
    assert run_registered_match(attempt((digest, digest)), store) is sentinel
    assert all(not path.exists() for path in paths)


def test_registered_execution_forwards_cancellation(tmp_path):
    store = FileArtifactStore(tmp_path)
    digest = store.put((ROOT / "bots" / "optimal.py").read_bytes())
    cancel = threading.Event()
    cancel.set()
    result = run_registered_match(attempt((digest, digest)), store, cancel=cancel)
    assert result.kind == OutcomeKind.INFRASTRUCTURE_FAILURE


def test_partial_write_error_does_not_leave_object(tmp_path, monkeypatch):
    from tournament_management.adapters import artifact_store

    store = FileArtifactStore(tmp_path)
    original = artifact_store.tempfile.NamedTemporaryFile

    class PartialWriter:
        def __enter__(self):
            self.stream = original(dir=tmp_path, prefix=".upload-", delete=False)
            self.name = self.stream.name
            return self

        def write(self, content):
            self.stream.write(content[:3])
            self.stream.flush()
            raise OSError("disk full")

        def __exit__(self, *args):
            self.stream.close()

    monkeypatch.setattr(
        artifact_store.tempfile, "NamedTemporaryFile", lambda **kwargs: PartialWriter()
    )
    with pytest.raises(OSError, match="disk full"):
        store.put(b"# complete source\n")
    assert list(tmp_path.iterdir()) == []
