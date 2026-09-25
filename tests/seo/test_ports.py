import pytest

from next.seo import urls, views
from next.seo.manager import seo_manager
from next.seo.ports import SeoRoutesImpl
from tests.seo.trees import routed, write_tree


class TestSeoRoutesImpl:
    """The routes port answers only the `next.seo.urls` routes a source backs."""

    @pytest.fixture(autouse=True)
    def _fresh_manager(self):
        seo_manager.reset()
        yield
        seo_manager.reset()

    @pytest.mark.parametrize(
        ("sources", "names"),
        [
            ({}, []),
            ({"sitemap": ""}, ["sitemap", "sitemap_section"]),
            ({"robots": ""}, ["robots"]),
            ({"robots_txt": b"User-agent: *\n"}, ["robots"]),
            ({"sitemap": "", "robots": ""}, ["sitemap", "sitemap_section", "robots"]),
        ],
        ids=["none", "sitemap", "robots-py", "robots-txt", "both"],
    )
    def test_a_route_is_served_only_with_its_source(
        self, tmp_path, sources, names
    ) -> None:
        with routed(write_tree(tmp_path / "pages", **sources)):
            patterns = SeoRoutesImpl().patterns()
        assert [pattern.name for pattern in patterns] == names

    def test_the_routes_are_those_of_the_seo_urls(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap="", robots="")):
            patterns = SeoRoutesImpl().patterns()
        assert patterns == urls.urlpatterns
        assert patterns is not urls.urlpatterns
        assert [pattern.callback for pattern in patterns] == [
            views.sitemap_view,
            views.sitemap_view,
            views.robots_view,
        ]
