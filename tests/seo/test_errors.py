from pathlib import Path

from next.seo import SitemapOriginError, SitemapTrailError


class TestSitemapOriginError:
    """The origin error names the tree and the setting that would fix it."""

    def test_names_the_root_and_the_setting(self) -> None:
        error = SitemapOriginError(Path("/site/pages"))
        assert error.root == Path("/site/pages")
        assert "the sitemap of /site/pages needs a request" in str(error)
        assert "NEXT_FRAMEWORK['METADATA']['DEFAULTS']['base']" in str(error)

    def test_is_a_value_error(self) -> None:
        assert isinstance(SitemapOriginError(Path("/x")), ValueError)


class TestSitemapTrailError:
    """The trail error names the trail and the `sitemap.py` declaring it."""

    def test_names_the_trail_the_module_and_its_tree(self) -> None:
        error = SitemapTrailError(Path("/site/pages/sitemap.py"), "posts/[slug]")
        assert error.file == Path("/site/pages/sitemap.py")
        assert error.trail == "posts/[slug]"
        assert str(error) == (
            "@sitemap.items('posts/[slug]') in /site/pages/sitemap.py names a trail "
            "no page under /site/pages routes"
        )

    def test_is_a_value_error(self) -> None:
        assert isinstance(SitemapTrailError(Path("/x/sitemap.py"), "a"), ValueError)
