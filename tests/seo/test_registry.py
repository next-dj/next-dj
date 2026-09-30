from pathlib import Path

from next.seo.registry import (
    SitemapItemsConflict,
    SitemapItemsEntry,
    SitemapItemsRegistry,
)


def _one() -> list[dict[str, str]]:
    return []


def _two() -> list[dict[str, str]]:
    return []


SITEMAP = Path("/a/sitemap.py")


def _entry(file: Path, trail: str, func=_one, **extra) -> SitemapItemsEntry:
    return SitemapItemsEntry(file=file, trail=trail, func=func, **extra)


class TestSitemapItemsRegistry:
    """The registry scopes entries by file, keeps their order and moves its version."""

    def test_starts_empty(self) -> None:
        registry = SitemapItemsRegistry()
        assert registry.entries_for(SITEMAP) == ()
        assert registry.version == 0

    def test_register_keeps_order_and_scopes_by_file(self) -> None:
        registry = SitemapItemsRegistry()
        posts = _entry(SITEMAP, "posts/[slug]")
        docs = _entry(Path("/b/sitemap.py"), "docs/[slug]", _two)
        tags = _entry(SITEMAP, "tags/[tag]", _two, section="tags", lastmod="updated")
        for entry in (posts, docs, tags, _entry(Path("/a/page.py"), "about")):
            registry.register(entry)
        assert registry.entries_for(SITEMAP) == (posts, tags)
        assert registry.entries_for(Path("/b/sitemap.py")) == (docs,)
        assert tags.section == "tags"
        assert tags.lastmod == "updated"
        assert posts.kwargs is None

    def test_a_repeat_registration_replaces_in_place(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        registry.register(_entry(SITEMAP, "tags/[tag]"))
        registry.register(_entry(SITEMAP, "posts/[slug]", _two))
        assert [entry.func for entry in registry.entries_for(SITEMAP)] == [_two, _one]

    def test_a_second_callable_on_one_trail_is_a_conflict(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        assert registry.conflicts() == ()
        registry.register(_entry(SITEMAP, "posts/[slug]", _two))
        assert registry.conflicts() == (
            SitemapItemsConflict(SITEMAP, "posts/[slug]", "_one", "_two"),
        )

    def test_forget_and_reset_drop_the_conflicts(self) -> None:
        registry = SitemapItemsRegistry()
        other = Path("/b/sitemap.py")
        for file in (SITEMAP, other):
            registry.register(_entry(file, "posts/[slug]"))
            registry.register(_entry(file, "posts/[slug]", _two))
        registry.forget(SITEMAP)
        assert [conflict.file for conflict in registry.conflicts()] == [other]
        registry.reset()
        assert registry.conflicts() == ()

    def test_forget_drops_one_file_and_moves_the_version(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        kept = _entry(Path("/b/sitemap.py"), "posts/[slug]")
        registry.register(kept)
        before = registry.version
        registry.forget(SITEMAP)
        assert registry.entries_for(SITEMAP) == ()
        assert registry.entries_for(Path("/b/sitemap.py")) == (kept,)
        assert registry.version == before + 1
        registry.register(_entry(SITEMAP, "posts/[slug]", _two))
        assert registry.entries_for(SITEMAP)[0].func is _two

    def test_forgetting_a_file_that_registered_nothing_moves_nothing(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        before = registry.version
        registry.forget(Path("/c/sitemap.py"))
        assert registry.version == before

    def test_a_reset_drops_everything_and_moves_the_version(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        registry.reset()
        assert registry.version == 2
        assert registry.entries_for(SITEMAP) == ()

    def test_registered_names_group_by_registering_file(self) -> None:
        registry = SitemapItemsRegistry()
        registry.register(_entry(SITEMAP, "posts/[slug]"))
        registry.register(_entry(SITEMAP, "tags/[tag]", _two))
        registry.register(_entry(Path("/b/helpers.py"), "docs/[slug]"))
        assert registry.registered_names() == {
            SITEMAP: ("_one", "_two"),
            Path("/b/helpers.py"): ("_one",),
        }
