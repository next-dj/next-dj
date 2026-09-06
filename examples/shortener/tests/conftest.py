from collections.abc import Callable

import pytest
from shortener.models import Link


@pytest.fixture()
def make_link() -> Callable[..., Link]:
    # The URL defaults to one derived from the slug, so tests only spell it out
    # when the value itself is under assertion.
    def _make(slug: str = "abc123", *, url: str | None = None, clicks: int = 0) -> Link:
        return Link.objects.create(
            slug=slug, url=url or f"https://example.com/{slug}", clicks=clicks
        )

    return _make
