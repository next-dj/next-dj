from unittest.mock import patch

from django.core.checks import Error, run_checks
from django.test import Client

from next.checks import reset_check_caches
from next.seo import checks
from next.seo.checks import roots as seo_roots
from next.seo.checks.roots import (
    declares_sitemap,
    items_trails,
    loaded_seo_roots,
    published_sources,
    robots_modules,
    serves_robots,
    serves_sitemap,
    sitemap_roots,
)
from next.seo.manager import seo_manager
from next.seo.registry import sitemap_items_registry
from tests.seo.sources import CALLS
from tests.support import POSTS_ITEMS, routed, write_tree


COUNTED_SITEMAP = (
    "from tests.seo.sources import CALLS\nCALLS.append('sitemap')\n" + POSTS_ITEMS
)
CHECKS = [getattr(checks, name) for name in checks.__all__]
COUNTED_ROBOTS = "from tests.seo.sources import CALLS\nCALLS.append('robots')\n"


class TestLoadedSeoRoots:
    """The checks read the trees the routes discovered, never a discovery of their own."""

    def test_every_tree_is_read_once(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root, root):
            roots = loaded_seo_roots()
        assert [entry.path for entry in roots] == [root]
        assert set(roots[0].trails) == {"", "about"}
        assert roots[0].section == "pages"

    def test_the_roots_are_the_ones_the_routes_serve(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            assert loaded_seo_roots() is seo_manager.roots()

    def test_a_check_run_executes_each_source_at_most_once(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=COUNTED_SITEMAP,
            robots=COUNTED_ROBOTS,
        )
        CALLS.clear()
        try:
            with routed(root):
                run_checks()
                ran = sorted(CALLS)
                entries = sitemap_items_registry.entries_for(root / "sitemap.py")
                run_checks()
                Client().get("/sitemap.xml")
                again = sitemap_items_registry.entries_for(root / "sitemap.py")
            assert ran == ["robots", "sitemap"]
            assert sorted(CALLS) == ["robots", "sitemap"]
            assert again == entries
        finally:
            CALLS.clear()

    def test_a_reset_discovers_the_trees_again(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            first = loaded_seo_roots()
            assert loaded_seo_roots() is first
            reset_check_caches()
            assert loaded_seo_roots() is not first

    def test_a_router_that_fails_to_start_reads_no_tree(self, tmp_path) -> None:
        failed = (None, [Error("router down", id="next.E007")])
        root = write_tree(tmp_path / "pages", sitemap="")
        with (
            routed(root),
            patch.object(seo_roots, "get_router_manager", return_value=failed),
        ):
            assert loaded_seo_roots() == ()
            for check in CHECKS:
                assert check() == [], check.__name__


class TestSourceModules:
    """Only a source that imported is handed to the checks reading its module."""

    def test_imported_sources_are_paired_with_their_modules(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=POSTS_ITEMS,
            robots="rules = []\n",
        )
        with routed(root):
            roots = loaded_seo_roots()
            [(sitemap_root, sitemap)] = sitemap_roots(roots)
            [(robots_path, robots)] = robots_modules(roots)
            trails = items_trails(roots[0])
        assert sitemap_root is roots[0]
        assert sitemap.__file__ == str(root / "sitemap.py")
        assert (robots_path, robots.rules) == (root / "robots.py", [])
        assert trails == {"posts/[slug]"}

    def test_a_broken_source_holds_its_route_but_yields_no_module(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="1/0\n", robots="1/0\n")
        with routed(root):
            roots = loaded_seo_roots()
            held = (declares_sitemap(roots), serves_robots(roots))
        assert held == (True, True)
        assert list(sitemap_roots(roots)) == []
        assert list(robots_modules(roots)) == []


class TestServedRoutes:
    """Each predicate matches the mount decision of the route it describes."""

    def test_a_bare_tree_serves_nothing(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages")):
            roots = loaded_seo_roots()
            served = (declares_sitemap(roots), serves_sitemap(), serves_robots(roots))
            published = published_sources(roots)
        assert served == (False, False, False)
        assert published == []

    def test_every_source_serves_its_route(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=b"User-agent: *\n")
        with routed(root):
            roots = loaded_seo_roots()
            served = (declares_sitemap(roots), serves_sitemap(), serves_robots(roots))
            published = published_sources(roots)
        assert served == (True, True, True)
        assert published == ["a sitemap", "a robots.txt"]
