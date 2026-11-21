"""Plazos del intento: fórmula documentada que avisa sin impedir el arranque."""

import logging

import pytest

from tournament_management.config import Settings
from tournament_management.runtime import application_services


def test_defaults_satisfy_the_formula():
    # 90 s >= 60 s de partida + 2 x 10 s de descarga + 10 s de margen.
    assert Settings().timing_warnings() == []


def test_short_attempt_timeout_warns_but_is_accepted():
    settings = Settings(attempt_timeout_ms=70_000)
    (message,) = settings.timing_warnings()
    assert "90000" in message


def test_longer_downloads_raise_the_minimum():
    assert Settings(artifact_download_timeout_ms=20_000).timing_warnings()


def test_worker_delay_is_bounded_and_extends_the_attempt_deadline():
    from dataclasses import fields

    from tournament_management.execution.policy import ExecutionPolicy

    for value in (-1, 60_001):
        with pytest.raises(ValueError, match="TM_WORKER_DELAY_MS"):
            Settings(worker_delay_ms=value)
    settings = Settings.load({}, {"TM_WORKER_DELAY_MS": "20000"})
    # runtime.py construye la política copiando los campos homónimos de Settings.
    policy = ExecutionPolicy(**{f.name: getattr(settings, f.name) for f in fields(ExecutionPolicy)})
    assert policy.attempt_deadline_ms == 90_000 + 20_000
    assert settings.timing_warnings() == []  # la espera no recorta el margen de la partida


def test_startup_logs_the_warning_and_continues(tmp_path, caplog):
    settings = Settings(attempt_timeout_ms=70_000, data_dir=str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="tournament_management.runtime"):
        with application_services(settings) as services:
            assert services is None  # sin base de datos, pero arrancó
    assert any("TM_ATTEMPT_TIMEOUT_MS=70000" in r.getMessage() for r in caplog.records)
