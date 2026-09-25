import logging
from pathlib import Path

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.seo import seo_manager, sitemap
from next.seo.manager import SeoManager, SitemapDeclaration
from next.seo.registry import sitemap_items_registry
from next.seo.robots import RobotsFile, RobotsRules
from next.urls import router_manager
from next.urls.signals import router_reloaded
from tests.seo.trees import WITH_BASE, listed_elsewhere, routed, write_tree


IMPORTED = """
from next.seo import sitemap
from tests.seo.trees import listed_elsewhere

sitemap.items("posts/[slug]")(listed_elsewhere)
"""


class TestSitemapDeclaration:
    """`sitemap.items` registers under the file running the decorator."""

    def test_items_registers_under_the_running_file(self) -> None:
        def posts():
            return []

        assert sitemap.items("posts/[slug]")(posts) is posts
        here = Path(__file__)
        assert ("posts/[slug]", posts) in sitemap_items_registry.entries_for(here)

    def test_the_exported_object_is_a_declaration(self) -> None:
        assert isinstance(sitemap, SitemapDeclaration)

    def test_an_imported_callable_lists_the_urls_of_the_importing_tree(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=IMPORTED)
        with routed(root, **WITH_BASE):
            items = seo_manager.sitemaps()["pages"].items()
        assert [(item.trail, dict(item.kwargs)) for item in items] == [
            ("posts/[slug]", {"slug": "elsewhere"})
        ]
        assert sitemap_items_registry.entries_for(root / "sitemap.py") == (
            ("posts/[slug]", listed_elsewhere),
        )


class TestSeoManager:
    """The manager memoises the roots and answers the sitemaps, cache and robots."""

    def test_roots_are_memoised_until_a_reset(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            first = seo_manager.roots()
            assert seo_manager.roots() is first
            before = seo_manager.version
            seo_manager.reset()
            assert seo_manager.version != before
            assert seo_manager.roots() is not first
            assert seo_manager.roots() == first

    def test_two_managers_never_share_a_version(self) -> None:
        assert SeoManager().version != SeoManager().version

    def test_has_sitemap_follows_the_sources(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots="")):
            assert seo_manager.has_sitemap() is False
        with routed(write_tree(tmp_path / "other", sitemap="")):
            assert seo_manager.has_sitemap() is True

    def test_noindex_turns_the_sitemap_off(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 30\n")
        with routed(root, METADATA={"NOINDEX": True}):
            assert seo_manager.has_sitemap() is False
            assert seo_manager.sitemaps() == {}

    def test_sitemaps_are_fresh_per_call_and_keyed_by_section(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            first = seo_manager.sitemaps()
            assert list(first) == ["pages"]
            assert seo_manager.sitemaps()["pages"] is not first["pages"]

    def test_a_sitemap_removed_after_discovery_drops_out_on_reset(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 30\n")
        with routed(root):
            assert seo_manager.cache_seconds() == 30
            (root / "sitemap.py").unlink()
            assert seo_manager.cache_seconds() == 30
            seo_manager.reset()
            assert seo_manager.sitemaps() == {}
            assert seo_manager.cache_seconds() is None

    def test_cache_seconds_is_the_shortest_declared(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", sitemap="cache = 600\n")
        second = write_tree(tmp_path / "b", sitemap="cache = 60\n")
        third = write_tree(tmp_path / "c", sitemap="cache = 'no'\n")
        with routed(first, second, third):
            assert seo_manager.cache_seconds() == 60
        with routed(third):
            assert seo_manager.cache_seconds() is None

    def test_a_bool_cache_caches_nothing(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap="cache = True\n")):
            assert seo_manager.cache_seconds() is None

    def test_a_sitemap_that_failed_to_import_caches_nothing(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="raise RuntimeError('x')\n")
        with routed(root):
            assert seo_manager.cache_seconds() is None

    def test_robots_py_wins_over_robots_txt_in_one_root(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"x")
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            source = seo_manager.robots_source()
            assert seo_manager.robots_source() is source
        assert isinstance(source, RobotsRules)
        assert source.path == root / "robots.py"
        assert caplog.text.count("/robots.txt has 2 sources") == 1
        assert f"{root / 'robots.py'} serves it" in caplog.text
        assert f"ignored: {root / 'robots.txt'}" in caplog.text

    def test_the_first_root_wins_across_roots(self, tmp_path, caplog) -> None:
        first = write_tree(tmp_path / "a", robots_txt=b"first")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second), caplog.at_level(logging.WARNING, logger="next.seo"):
            source = seo_manager.robots_source()
        assert isinstance(source, RobotsFile)
        assert source.path == first / "robots.txt"
        assert f"ignored: {second / 'robots.py'}" in caplog.text

    def test_a_removed_robots_py_falls_back_to_the_file_on_reset(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"x")
        with routed(root):
            seo_manager.roots()
            (root / "robots.py").unlink()
            assert isinstance(seo_manager.robots_source(), RobotsRules)
            seo_manager.reset()
            assert isinstance(seo_manager.robots_source(), RobotsFile)

    def test_no_source_answers_none(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages")):
            assert seo_manager.robots_source() is None


class TestResets:
    """Router and settings reloads reset the manager and keep the items registry."""

    def test_the_manager_listens_to_the_router_alone(self) -> None:
        assert seo_manager.reset in [entry[1]() for entry in router_reloaded.receivers]
        assert seo_manager.reset not in [
            entry[1]() for entry in settings_reloaded.receivers
        ]

    def test_a_router_reload_moves_the_manager_version(self) -> None:
        before = seo_manager.version
        router_manager.reload()
        assert seo_manager.version != before

    def test_a_settings_reload_resets_the_manager_but_keeps_the_registry(
        self, tmp_path
    ) -> None:
        """A memoised `sitemap.py` never re-registers, so its entries must survive."""

        def posts():
            return []

        sitemap_items_registry.register(tmp_path / "sitemap.py", "posts/[slug]", posts)
        before = seo_manager.version
        next_framework_settings.reload()
        assert seo_manager.version != before
        assert sitemap_items_registry.entries_for(tmp_path / "sitemap.py") == (
            ("posts/[slug]", posts),
        )

    def test_reset_accepts_signal_kwargs(self) -> None:
        before = seo_manager.version
        seo_manager.reset(sender=object(), signal=None)
        assert seo_manager.version != before
