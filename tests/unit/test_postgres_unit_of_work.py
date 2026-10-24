"""El ciclo de vida de la transacción no depende del repositorio concreto."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tournament_management.adapters import postgres_connection, postgres_store


@pytest.fixture
def connection(monkeypatch):
    connection = Mock()
    psycopg = SimpleNamespace(connect=Mock(return_value=connection), Error=Exception)
    monkeypatch.setattr(postgres_store, "driver", lambda: (psycopg, object()))
    return connection


def test_unit_of_work_rolls_back_without_commit(connection):
    with postgres_store.PostgresUnitOfWork("postgresql://example") as work:
        assert work.matches is not None
        assert work.tournaments is not None
        assert work.events is not None
    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()
    connection.close.assert_called_once_with()


def test_unit_of_work_commits_and_closes(connection):
    with postgres_store.PostgresUnitOfWork("postgresql://example") as work:
        work.commit()
    connection.commit.assert_called_once_with()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once_with()


def test_unit_of_work_rolls_back_when_exception_occurs(connection):
    with pytest.raises(ValueError), postgres_store.PostgresUnitOfWork("postgresql://example"):
        raise ValueError("failure")
    connection.rollback.assert_called_once_with()
    connection.close.assert_called_once_with()


def test_existing_postgres_entry_points_remain_available():
    assert postgres_store.check_connection is postgres_connection.check_connection
    assert postgres_store.migrate is postgres_connection.migrate
