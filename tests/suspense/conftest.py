from collections.abc import Iterator

import pytest
from django.core.signals import request_finished
from django.db import close_old_connections

from next.suspense.executors import _asgiref_gaps


@pytest.fixture(autouse=True)
def _unchecked_asgiref() -> Iterator[None]:
    """Forget the cached asgiref verdict, so each test checks the attributes it sees."""
    _asgiref_gaps.cache_clear()
    yield
    _asgiref_gaps.cache_clear()


@pytest.fixture(autouse=True)
def _closed_as_the_test_client_closes() -> Iterator[None]:
    """Close a response a test closes by hand as Django's test client closes one.

    The in-memory test database stays connected, which a test without access cannot read.
    """
    request_finished.disconnect(close_old_connections)
    try:
        yield
    finally:
        request_finished.connect(close_old_connections)
