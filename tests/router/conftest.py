from collections.abc import Iterator

import pytest

from next.router import links


@pytest.fixture(autouse=True)
def _forgotten_links() -> Iterator[None]:
    """Start and end each test with no container or link the current-page pass walked."""
    links._walked.clear()
    links._known_link.cache_clear()
    yield
    links._walked.clear()
    links._known_link.cache_clear()
