from pathlib import Path

import pytest

from next.seo import (
    RobotsRuleError,
    SeoSourceImportError,
    SitemapEntryError,
    SitemapTrailError,
)


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
        assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    ("error_class", "owner"),
    [(SitemapEntryError, "SitemapEntry"), (RobotsRuleError, "RobotsRule")],
)
def test_a_value_error_names_the_field_the_value_and_the_shape(
    error_class: type[SitemapEntryError | RobotsRuleError], owner: str
) -> None:
    error = error_class(7, field="priority", expected="a number")
    assert (error.field, error.value) == ("priority", 7)
    assert str(error) == f"{owner}.priority is 7, expected a number"
    assert isinstance(error, ValueError)


class TestSeoSourceImportError:
    """The import error names the failing file and carries no cause of its own."""

    def test_names_the_file(self) -> None:
        error = SeoSourceImportError(Path("/site/pages/robots.py"))
        assert error.path == Path("/site/pages/robots.py")
        assert str(error) == "/site/pages/robots.py failed to import"
