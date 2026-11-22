from collections.abc import Iterator

import pytest

from next.router import current


@pytest.fixture(autouse=True)
def _forgotten_links() -> Iterator[None]:
    """Start and end each test with no container or link the current-page pass walked."""
    current._walked.clear()
    current._known_link.cache_clear()
    yield
    current._walked.clear()
    current._known_link.cache_clear()
