# Canonical scaffold: see docs/content/guide/testing.rst for the rationale.
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import django
import pytest
import time_machine
from django.conf import settings
from django.core.cache import cache

from next.testing import NextClient, eager_load_pages


EXAMPLE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_ROOT.parent.parent

for path in (EXAMPLE_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if not settings.configured:
    django.setup()


@pytest.fixture(autouse=True, scope="session")
def _load_pages() -> None:
    eager_load_pages(EXAMPLE_ROOT / "obs" / "dashboards")


@pytest.fixture(autouse=True)
def _isolate() -> None:
    cache.clear()


@pytest.fixture()
def client() -> NextClient:
    return NextClient()


@pytest.fixture()
def frozen_now() -> Callable[[datetime | str], "Iterator[time_machine.Coordinates]"]:
    """Return a context manager that pins `obs.metrics._now` to a moment.

    The fixture wraps `time_machine.travel(..., tick=False)` so bucket-
    boundary tests can step the clock minute by minute without races.
    Usage::

        def test_x(frozen_now):
            with frozen_now("2026-05-08T12:00:00+00:00") as traveller:
                metrics.incr("k", "x")
                traveller.move_to("2026-05-08T12:01:00+00:00")
                metrics.incr("k", "x")
    """

    @contextmanager
    def travel(moment: datetime | str) -> "Iterator[time_machine.Coordinates]":
        with time_machine.travel(moment, tick=False) as traveller:
            yield traveller

    return travel
