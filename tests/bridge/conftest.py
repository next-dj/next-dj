from collections.abc import Iterator

import pytest

from next.bridge.hops import check_asgiref, verdict


@pytest.fixture(autouse=True)
def _unjudged() -> Iterator[None]:
    """Forget the cached verdict, so each test judges the asgiref it sees."""
    verdict.cache_clear()
    check_asgiref.cache_clear()
    yield
    verdict.cache_clear()
    check_asgiref.cache_clear()
