from pathlib import Path

from next.seo.registry import SitemapItemsRegistry
from next.seo.signals import sitemap_items_registered
from next.testing import capture_signals


def _one() -> list[dict[str, str]]:
    return []


def _two() -> list[dict[str, str]]:
    return []


class TestSitemapItemsRegistry:
    def test_starts_empty(self) -> None:
        registry = SitemapItemsRegistry()
        assert registry.entries_for(Path("/a")) == ()
        assert registry.version == 0

    def test_register_keeps_order_and_scopes_by_root(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a"), "posts/[slug]", _one)
        registry.register(Path("/b"), "docs/[slug]", _two)
        registry.register(Path("/a"), "tags/[tag]", _two)
        assert registry.entries_for(Path("/a")) == (
            ("posts/[slug]", _one),
            ("tags/[tag]", _two),
        )
        assert registry.entries_for(Path("/b")) == (("docs/[slug]", _two),)

    def test_a_repeat_registration_replaces_in_place(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a"), "posts/[slug]", _one)
        registry.register(Path("/a"), "tags/[tag]", _one)
        registry.register(Path("/a"), "posts/[slug]", _two)
        assert registry.entries_for(Path("/a")) == (
            ("posts/[slug]", _two),
            ("tags/[tag]", _one),
        )

    def test_every_write_moves_the_version(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a"), "posts/[slug]", _one)
        after_register = registry.version
        registry.reset()
        assert after_register == 1
        assert registry.version == 2
        assert registry.entries_for(Path("/a")) == ()

    def test_register_sends_the_signal(self) -> None:
        registry = SitemapItemsRegistry()
        with capture_signals(sitemap_items_registered) as recorder:
            registry.register(Path("/a"), "posts/[slug]", _one)
        event = recorder.first_for(sitemap_items_registered)
        assert event.sender is SitemapItemsRegistry
        assert event.kwargs == {
            "root": Path("/a"),
            "trail": "posts/[slug]",
            "func": _one,
        }

    def test_reset_accepts_signal_kwargs(self) -> None:
        registry = SitemapItemsRegistry()
        registry.reset(sender=object(), signal=None)
        assert registry.version == 1
