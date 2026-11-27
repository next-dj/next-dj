from collections.abc import Callable, Iterator

import pytest

from next.defer.coordinator.drivers import loop
from next.defer.manager import defer_manager
from tests.defer.coordinator.drivers.doubles import INSTALLED, CountingPool, install


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run each coordinator on the backends its test installed, the process ones else."""
    executor, admission = defer_manager.executor, defer_manager.admission
    permits = loop.request_permits
    monkeypatch.setattr(
        defer_manager, "executor", lambda: INSTALLED.executor or executor()
    )
    monkeypatch.setattr(
        defer_manager, "admission", lambda: INSTALLED.admission or admission()
    )
    monkeypatch.setattr(loop, "request_permits", lambda: INSTALLED.permits or permits())
    yield
    install(None, None, None)


@pytest.fixture()
def pools() -> Iterator[Callable[[int], CountingPool]]:
    """Build pools of a given size and stop every one after the test."""
    made: list[CountingPool] = []

    def make(workers: int = 16) -> CountingPool:
        executor = CountingPool(workers)
        made.append(executor)
        return executor

    yield make
    for executor in made:
        executor.shutdown(5)
