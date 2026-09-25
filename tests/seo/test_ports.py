from next.seo import seo_manager, views
from next.seo.ports import SeoRoutesImpl
from tests.seo.trees import routed, write_tree


class TestSeoRoutesImpl:
    def test_a_sitemap_alone_adds_the_two_sitemap_routes(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap="")):
            patterns = SeoRoutesImpl().patterns()
        assert [pattern.name for pattern in patterns] == ["sitemap", "sitemap_section"]
        assert {pattern.callback for pattern in patterns} == {views.sitemap}

    def test_a_robots_source_alone_adds_the_robots_route(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots_txt=b"x")):
            patterns = SeoRoutesImpl().patterns()
        assert [pattern.name for pattern in patterns] == ["robots"]
        assert patterns[0].callback is views.robots

    def test_both_sources_add_all_three(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap="", robots="")):
            names = [pattern.name for pattern in SeoRoutesImpl().patterns()]
        assert names == ["sitemap", "sitemap_section", "robots"]

    def test_version_follows_the_manager(self) -> None:
        source = SeoRoutesImpl().version_source()
        assert source is seo_manager
        before = source.version
        seo_manager.reset()
        assert source.version != before
