from pathlib import Path

from next.seo import sitemap
from next.seo.decorators import SitemapDeclaration
from next.seo.registry import sitemap_items_registry


def _kwargs(row: object) -> dict[str, object]:
    return {"slug": row}


class TestSitemapItems:
    """`@sitemap.items` registers for the file running it, with every option."""

    def test_the_decorator_binds_the_running_file_and_returns_the_callable(
        self,
    ) -> None:
        def posts() -> list[str]:
            return []

        try:
            assert sitemap.items("posts/[slug]")(posts) is posts
            [entry] = sitemap_items_registry.entries_for(Path(__file__))
        finally:
            sitemap_items_registry.forget(Path(__file__))
        assert (entry.trail, entry.func) == ("posts/[slug]", posts)
        assert (entry.section, entry.kwargs, entry.lastmod) == (None, None, None)

    def test_the_options_travel_on_the_entry(self) -> None:
        def notes() -> list[str]:
            return []

        declaration = SitemapDeclaration()
        try:
            declaration.items(
                "notes/[slug]", kwargs=_kwargs, lastmod="updated_at", section="notes"
            )(notes)
            [entry] = sitemap_items_registry.entries_for(Path(__file__))
        finally:
            sitemap_items_registry.forget(Path(__file__))
        assert entry.kwargs is _kwargs
        assert entry.lastmod == "updated_at"
        assert entry.section == "notes"
