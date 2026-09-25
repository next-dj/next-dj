from pathlib import Path

from next.seo.registry import SitemapItemsRegistry


def _one() -> list[dict[str, str]]:
    return []


def _two() -> list[dict[str, str]]:
    return []


class TestSitemapItemsRegistry:
    """The registry scopes entries by file, keeps their order and moves its version."""

    def test_starts_empty(self) -> None:
        registry = SitemapItemsRegistry()
        assert registry.entries_for(Path("/a/sitemap.py")) == ()
        assert registry.version == 0

    def test_register_keeps_order_and_scopes_by_file(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a/sitemap.py"), "posts/[slug]", _one)
        registry.register(Path("/b/sitemap.py"), "docs/[slug]", _two)
        registry.register(Path("/a/sitemap.py"), "tags/[tag]", _two)
        registry.register(Path("/a/page.py"), "about", _one)
        assert registry.entries_for(Path("/a/sitemap.py")) == (
            ("posts/[slug]", _one),
            ("tags/[tag]", _two),
        )
        assert registry.entries_for(Path("/b/sitemap.py")) == (("docs/[slug]", _two),)

    def test_a_repeat_registration_replaces_in_place(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a/sitemap.py"), "posts/[slug]", _one)
        registry.register(Path("/a/sitemap.py"), "tags/[tag]", _one)
        registry.register(Path("/a/sitemap.py"), "posts/[slug]", _two)
        assert registry.entries_for(Path("/a/sitemap.py")) == (
            ("posts/[slug]", _two),
            ("tags/[tag]", _one),
        )

    def test_every_write_moves_the_version(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a/sitemap.py"), "posts/[slug]", _one)
        after_register = registry.version
        registry.reset()
        assert after_register == 1
        assert registry.version == 2
        assert registry.entries_for(Path("/a/sitemap.py")) == ()

    def test_registered_names_group_by_registering_file(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(Path("/a/sitemap.py"), "posts/[slug]", _one)
        registry.register(Path("/a/sitemap.py"), "tags/[tag]", _two)
        registry.register(Path("/b/helpers.py"), "docs/[slug]", _one)
        assert registry.registered_names() == {
            Path("/a/sitemap.py"): ("_one", "_two"),
            Path("/b/helpers.py"): ("_one",),
        }
