from pathlib import Path

from next.seo import SeoBaseError, SitemapTrailError


class TestSeoBaseError:
    def test_names_the_root_and_the_setting(self) -> None:
        error = SeoBaseError(Path("/site/pages"))
        assert error.root == Path("/site/pages")
        assert "the sitemap of /site/pages needs a request" in str(error)
        assert "NEXT_FRAMEWORK['METADATA']['DEFAULTS']['base']" in str(error)

    def test_is_a_value_error(self) -> None:
        assert isinstance(SeoBaseError(Path("/x")), ValueError)


class TestSitemapTrailError:
    def test_names_the_trail_and_the_module(self) -> None:
        error = SitemapTrailError(Path("/site/pages"), "posts/[slug]")
        assert error.root == Path("/site/pages")
        assert error.trail == "posts/[slug]"
        assert "@sitemap.items('posts/[slug]') in /site/pages/sitemap.py" in str(error)

    def test_is_a_value_error(self) -> None:
        assert isinstance(SitemapTrailError(Path("/x"), "a"), ValueError)
