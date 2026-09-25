from pathlib import Path

from next.seo import sitemap
from next.seo.registry import SitemapItemsRegistry
from next.testing import SignalRecorder


def _posts() -> list[dict[str, str]]:
    return []


class TestSitemapItemsRegisteredSignal:
    """``sitemap_items_registered`` fires once per `@sitemap.items` registration."""

    def test_register_sends_the_file_the_trail_and_the_callable(
        self, capture_sitemap_items_registered: SignalRecorder
    ) -> None:
        SitemapItemsRegistry().register(Path("/a/sitemap.py"), "posts/[slug]", _posts)
        assert len(capture_sitemap_items_registered) == 1
        event = capture_sitemap_items_registered.events[0]
        assert event.sender is SitemapItemsRegistry
        assert event.kwargs == {
            "file": Path("/a/sitemap.py"),
            "trail": "posts/[slug]",
            "func": _posts,
        }

    def test_the_decorator_fires_it_for_the_declaring_file(
        self, capture_sitemap_items_registered: SignalRecorder
    ) -> None:
        sitemap.items("posts/[slug]")(_posts)
        event = capture_sitemap_items_registered.events[0]
        assert event.kwargs["file"] == Path(__file__)

    def test_a_reset_sends_nothing(
        self, capture_sitemap_items_registered: SignalRecorder
    ) -> None:
        SitemapItemsRegistry().reset()
        assert len(capture_sitemap_items_registered) == 0
