from collections.abc import Callable, Iterator

import pytest

from tests.suspense.coordinator.drivers.doubles import CountingPool


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
