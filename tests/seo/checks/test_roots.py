from unittest.mock import patch

from django.core.checks import run_checks

from next.checks import NEXT, SEO, reset_check_caches
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
from tests.support import POSTS_ITEMS, routed, write_tree


class TestLoadedSeoRoots:
    """The routed trees are discovered once per check run, not once per check."""

    def test_every_tree_is_read_once(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root, root):
            init_errors, roots = loaded_seo_roots()
        assert init_errors == []
        assert [entry.path for entry in roots] == [root]
        assert set(roots[0].trails) == {"", "about"}
        assert roots[0].section == "pages"

    def test_a_whole_run_discovers_the_trees_once(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=POSTS_ITEMS, robots="")
        with (
            routed(root),
            patch.object(
                seo_roots, "discover_seo_roots", wraps=seo_roots.discover_seo_roots
            ) as discover,
        ):
            run_checks(tags=[NEXT, SEO])
        assert discover.call_count == 1

    def test_a_reset_discovers_the_trees_again(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            _errors, first = loaded_seo_roots()
            assert loaded_seo_roots()[1] is first
            reset_check_caches()
            assert loaded_seo_roots()[1] is not first


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
            _errors, roots = loaded_seo_roots()
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
            _errors, roots = loaded_seo_roots()
            held = (declares_sitemap(roots), serves_robots(roots))
        assert held == (True, True)
        assert list(sitemap_roots(roots)) == []
        assert list(robots_modules(roots)) == []


class TestServedRoutes:
    """Each predicate answers the way the matching route decides to mount."""

    def test_a_bare_tree_serves_nothing(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages")):
            _errors, roots = loaded_seo_roots()
            served = (
                declares_sitemap(roots),
                serves_sitemap(roots),
                serves_robots(roots),
            )
            published = published_sources(roots)
        assert served == (False, False, False)
        assert published == []

    def test_every_source_serves_its_route(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=b"User-agent: *\n")
        with routed(root):
            _errors, roots = loaded_seo_roots()
            served = (
                declares_sitemap(roots),
                serves_sitemap(roots),
                serves_robots(roots),
            )
            published = published_sources(roots)
        assert served == (True, True, True)
        assert published == ["a sitemap", "a robots.txt"]
