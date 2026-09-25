from unittest.mock import patch

from django.core.checks import run_checks

from next.checks import NEXT, SEO, reset_check_caches
from next.seo.checks import (
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_sources_below_root,
    check_sitemap_items_files,
    roots as seo_roots,
)
from next.seo.checks.roots import loaded_seo_roots
from next.seo.registry import sitemap_items_registry
from tests.seo.trees import POSTS_ITEMS, listed_elsewhere, routed, write_tree
from tests.support import check_ids


FUTURE_ITEMS = "from __future__ import annotations\n" + POSTS_ITEMS


BAD_SITEMAP = """
changefreq = "sometimes"
priority = 2
limit = 0
cache = -1
exclude = "admin/**"
languages = ["en", 1]
i18n = "yes"
alternates = 1
x_default = None
protocol = "ftp"
"""


GOOD_SITEMAP = """
changefreq = "daily"
priority = 0.5
limit = 100
cache = 0
exclude = ["admin/**"]
languages = ["en", "de"]
i18n = True
alternates = True
x_default = False
protocol = "https"
"""


BAD_ROBOTS = "rules = [1]\nhost = 3\n"


GOOD_ROBOTS = """
from next.seo import Rule

rules = [Rule(user_agent="*", disallow=["/private/"])]
host = "acme.example"
"""


class TestModuleImports:
    """A `sitemap.py` or `robots.py` that fails to import is `next.E110`."""

    def test_a_sitemap_that_raises_is_reported_with_its_cause(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap='raise RuntimeError("boom")\n')
        with routed(root):
            messages = check_seo_module_imports()
        assert check_ids(messages) == ["next.E110"]
        assert "RuntimeError: boom" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")

    def test_deferred_annotations_are_refused_in_the_sitemap_alone(
        self, tmp_path
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=FUTURE_ITEMS,
            robots="from __future__ import annotations\n",
        )
        with routed(root):
            messages = check_seo_module_imports()
        assert check_ids(messages) == ["next.E110"]
        assert messages[0].obj == str(root / "sitemap.py")
        assert "__future__" in messages[0].msg

    def test_a_robots_py_that_raises_is_reported(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots='raise RuntimeError("boom")\n')
        with routed(root):
            [error] = check_seo_module_imports()
        assert error.id == "next.E110"
        assert error.obj == str(root / "robots.py")

    def test_importing_modules_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=POSTS_ITEMS,
            robots=GOOD_ROBOTS,
        )
        with routed(root):
            assert check_seo_module_imports() == []


class TestModuleAttributes:
    """Every module attribute of the wrong shape is named (`next.E113`)."""

    def test_every_wrong_shape_is_named(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=BAD_SITEMAP, robots=BAD_ROBOTS)
        with routed(root):
            messages = check_seo_module_attributes()
        assert check_ids(messages) == ["next.E113"] * 11
        named = [m.msg.split(" declares ")[1].split(" = ")[0] for m in messages]
        assert named == [
            "changefreq",
            "priority",
            "limit",
            "cache",
            "exclude",
            "languages",
            "i18n",
            "alternates",
            "protocol",
            "rules",
            "host",
        ]
        assert "one of always, daily" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")
        assert messages[-1].obj == str(root / "robots.py")

    def test_a_bool_cache_is_named(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = True\n")
        with routed(root):
            [error] = check_seo_module_attributes()
        assert error.id == "next.E113"
        assert "cache = True, expected seconds as an int, no bool" in error.msg

    def test_the_documented_shapes_pass(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=GOOD_SITEMAP, robots=GOOD_ROBOTS)
        with routed(root):
            assert check_seo_module_attributes() == []


class TestSourcesBelowRoot:
    """A source below the top of its tree is never served (`next.W102`)."""

    def test_a_source_below_the_top_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "blog"))
        (root / "blog" / "sitemap.py").write_text("")
        (root / "blog" / "robots.txt").write_bytes(b"")
        with routed(root):
            messages = check_seo_sources_below_root()
        assert check_ids(messages) == ["next.W102", "next.W102"]
        assert [m.obj for m in messages] == [
            str(root / "blog" / "sitemap.py"),
            str(root / "blog" / "robots.txt"),
        ]
        assert f"Move it to {root / 'sitemap.py'}" in messages[0].msg

    def test_sources_at_the_top_pass(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=b"")
        with routed(root):
            assert check_seo_sources_below_root() == []


class TestItemsFiles:
    """`@sitemap.items` run outside the root `sitemap.py` is an error (`next.E118`)."""

    def test_the_sitemap_py_of_a_root_is_silent(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_items_files() == []

    def test_a_sitemap_py_below_the_root_is_reported(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "blog"), sitemap="")
        nested = root / "blog" / "sitemap.py"
        sitemap_items_registry.register(nested, "blog", listed_elsewhere)
        with routed(root):
            [error] = check_sitemap_items_files()
        assert error.id == "next.E118"
        assert error.obj == str(nested)
        assert "runs @sitemap.items on listed_elsewhere" in error.msg
        assert "only the sitemap.py at the top of a routed page tree" in error.msg

    def test_a_callable_imported_into_the_root_sitemap_is_silent(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap="")
        sitemap_items_registry.register(
            root / "sitemap.py", "posts/[slug]", listed_elsewhere
        )
        with routed(root):
            assert check_sitemap_items_files() == []

    def test_a_decorator_run_by_a_page_py_is_reported(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        sitemap_items_registry.register(root / "page.py", "", listed_elsewhere)
        with routed(root):
            [error] = check_sitemap_items_files()
        assert error.obj == str(root / "page.py")


class TestLoadedSeoRootsOncePerRun:
    """The routed trees are discovered once per check run, not once per check."""

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
