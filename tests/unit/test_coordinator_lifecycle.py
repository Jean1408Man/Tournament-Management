"""Un fallo de recuperación impide arrancar y también libera el backend creado."""

import pytest

from tournament_management.config import Settings
from tournament_management.execution.backends.local import LocalExecution
from tournament_management.execution.recovery import StartupRecovery
from tournament_management.runtime import application_services


def test_failed_startup_closes_backend_without_starting_execution(tmp_path, monkeypatch):
    closed = []
    original = LocalExecution.close

    def close(self):
        original(self)
        closed.append(self)

    def fail(self):
        raise OSError("injected startup recovery failure")

    monkeypatch.setattr(LocalExecution, "close", close)
    monkeypatch.setattr(StartupRecovery, "recover", fail)
    settings = Settings(database_url="postgresql://unused/test", data_dir=str(tmp_path))
    with pytest.raises(OSError, match="injected startup recovery failure"):
        with application_services(settings):
            pytest.fail("Un arranque sin recuperación confirmada no puede atender trabajo")
    assert len(closed) == 1
    assert closed[0].workers()[0].running_attempts == 0
