from __future__ import annotations

from shortener.cache import flush_clicks
from shortener.models import Link


class TestLinkModel:
    """Pure-Python behaviour of the `Link` model."""

    def test_str_contains_slug_and_url(self) -> None:
        link = Link(slug="abc", url="https://example.com/a")
        assert str(link) == "abc → https://example.com/a"


class TestFlushClicksNoPending:
    """`flush_clicks` is a no-op when the cache has nothing to persist."""

    def test_returns_zero_when_cache_empty(self) -> None:
        Link.objects.create(slug="idle", url="https://example.com/i")
        assert flush_clicks() == 0
        assert Link.objects.get(slug="idle").clicks == 0
