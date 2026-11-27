from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django.db import transaction
from django.db.utils import ConnectionHandler
from pytest_django import DjangoDbBlocker

from next.defer.executors import LaneExecutor, hygiene
from tests.defer.executors.shared import Connections


@pytest.fixture()
def pools() -> Iterator[Callable[..., LaneExecutor]]:
    """Build pools with small options and stop every one after the test."""
    made: list[LaneExecutor] = []

    def make(
        *,
        max_workers: int = 2,
        max_queue: int = 8,
        conn_idle: float = 60.0,
        shutdown_timeout: float | None = None,
    ) -> LaneExecutor:
        options: dict[str, object] = {
            "MAX_WORKERS": max_workers,
            "MAX_QUEUE": max_queue,
            "CONN_IDLE": conn_idle,
        }
        if shutdown_timeout is not None:
            options["SHUTDOWN_TIMEOUT"] = shutdown_timeout
        executor = LaneExecutor({"OPTIONS": options})
        made.append(executor)
        return executor

    yield make
    for executor in made:
        executor.shutdown(5)


@pytest.fixture()
def fake_connections(monkeypatch: pytest.MonkeyPatch) -> Connections:
    handler = Connections()
    monkeypatch.setattr(hygiene, "connections", handler)
    return handler


@pytest.fixture()
def sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, django_db_blocker: DjangoDbBlocker
) -> Iterator[Callable[..., ConnectionHandler]]:
    """Point the pool and the transaction API at one file database built per test."""
    django_db_blocker.unblock()

    def make(**options: object) -> ConnectionHandler:
        database = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": tmp_path / "db",
            **options,
        }
        handler = ConnectionHandler({"default": database})
        monkeypatch.setattr(hygiene, "connections", handler)
        monkeypatch.setattr(transaction, "connections", handler)
        return handler

    yield make
    django_db_blocker.restore()
