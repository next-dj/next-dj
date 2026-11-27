from collections.abc import Iterator

import pytest
from django.core.signals import request_finished
from django.db import close_old_connections


@pytest.fixture(autouse=True)
def _own_deferred_components(fresh_deferred_components) -> None:
    """Give each bench its own tracker, since a shared flag slows every later bench."""


@pytest.fixture(autouse=True)
def _closed_without_the_database(django_db_blocker) -> Iterator[None]:
    """Close a streamed response over connections an earlier bench left open.

    The stream releases the connections its thread holds as its body ends, and the
    blocker would fail that check where a bench before this one used the database.
    """
    request_finished.disconnect(close_old_connections)
    with django_db_blocker.unblock():
        yield
    request_finished.connect(close_old_connections)
