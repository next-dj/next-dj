from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

import pytest
import time_machine


@pytest.fixture()
def frozen_now() -> Callable[[datetime | str], "Iterator[time_machine.Coordinates]"]:
    """Return a context manager that pins `obs.metrics._now` to a moment."""

    @contextmanager
    def travel(moment: datetime | str) -> "Iterator[time_machine.Coordinates]":
        with time_machine.travel(moment, tick=False) as traveller:
            yield traveller

    return travel
