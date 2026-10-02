from next.ports import SeoRoutes
from next.seo import urls
from next.seo.ports import SeoRoutesImpl
from tests.support import call_shape, port_methods, routed, write_tree


class TestSeoRoutesPort:
    """The SEO port hands the lazy urlpatterns its routes without an import."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert port_methods(SeoRoutes) == ["patterns"]

    def test_implementation_parameters_match_the_port(self) -> None:
        assert call_shape(SeoRoutesImpl, "patterns") == call_shape(
            SeoRoutes, "patterns"
        )


class TestSeoRoutesImpl:
    """The routes port answers the routes a source backs."""

    def test_the_routes_are_those_of_the_seo_urls(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root):
            patterns = SeoRoutesImpl().patterns()
        assert patterns == list(urls.PATTERNS[:3])
