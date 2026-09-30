from pathlib import Path

from django.test import override_settings

from next.seo import PageTreeSitemapBackend, sitemap
from next.seo.manager import seo_manager
from next.seo.registry import (
    SitemapItemsEntry,
    SitemapItemsRegistry,
    sitemap_items_registry,
)
from next.seo.signals import sitemap_backend_loaded
from next.testing import SignalRecorder, capture_signals


def _posts() -> list[dict[str, str]]:
    return []


class TestSitemapItemsRegisteredSignal:
    """``sitemap_items_registered`` fires once per `@sitemap.items` registration."""

    def test_register_sends_the_file_the_trail_and_the_callable(
        self, capture_sitemap_items_registered: SignalRecorder
    ) -> None:
        SitemapItemsRegistry().register(
            SitemapItemsEntry(Path("/a/sitemap.py"), "posts/[slug]", _posts)
        )
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
        try:
            sitemap.items("posts/[slug]")(_posts)
        finally:
            sitemap_items_registry.forget(Path(__file__))
        event = capture_sitemap_items_registered.events[0]
        assert event.kwargs["file"] == Path(__file__)

    def test_a_reset_and_a_forget_send_nothing(
        self, capture_sitemap_items_registered: SignalRecorder
    ) -> None:
        registry = SitemapItemsRegistry()
        registry.forget(Path("/a/sitemap.py"))
        registry.reset()
        assert len(capture_sitemap_items_registered) == 0


class TestSitemapBackendLoadedSignal:
    """``sitemap_backend_loaded`` fires once per backend the manager loads."""

    def test_a_load_sends_the_class_the_config_and_the_instance(self) -> None:
        entry = {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {"a": 1}}
        with (
            override_settings(NEXT_FRAMEWORK={"SEO": {"SITEMAP_BACKENDS": [entry]}}),
            capture_signals(sitemap_backend_loaded) as recorder,
        ):
            [backend] = seo_manager.backends
        event = recorder.first_for(sitemap_backend_loaded)
        assert event.sender is PageTreeSitemapBackend
        assert event.kwargs == {"config": entry, "instance": backend}
        assert backend.options == {"a": 1}
