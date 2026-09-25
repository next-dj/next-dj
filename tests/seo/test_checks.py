from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.core.checks import Tags
from django.core.checks.registry import registry as check_registry
from django.test import override_settings

from next.checks import NEXT, register_all, reset_check_caches
from next.seo.checks import (
    check_robots_disallow,
    check_robots_file,
    check_robots_single_source,
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_route_collisions,
    check_seo_routes_at_host_root,
    check_seo_sources_below_root,
    check_sitemap_dynamic_routes,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_labels,
    check_sitemap_templates,
    loaded_seo_roots,
)
from next.seo.manager import seo_manager
from next.seo.registry import sitemap_items_registry
from tests.seo.trees import routed, write_page, write_tree


CHECKS = [
    check_robots_disallow,
    check_robots_file,
    check_robots_single_source,
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_route_collisions,
    check_seo_routes_at_host_root,
    check_seo_sources_below_root,
    check_sitemap_dynamic_routes,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_labels,
    check_sitemap_templates,
]
SHADOWED_URLCONF = "tests.seo.shadowed_urls"
USER_URLCONF = "tests.seo.user_urls"
PREFIXED_URLCONF = "tests.seo.prefixed_urls"
PREFIX_ONLY_URLCONF = "tests.seo.prefix_only_urls"
NO_APP_DIRS = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {},
    }
]
NOINDEX = 'template = "x"\nmetadata = {"robots": {"index": False}}\n'
POSTS_ITEMS = """
from next.seo import sitemap


@sitemap.items("posts/[slug]")
def posts():
    yield {"slug": "a"}
"""
UNKNOWN_ITEMS = """
from next.seo import sitemap


@sitemap.items("nope/[slug]")
def nope():
    yield {"slug": "a"}
"""
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


def _disallow(*prefixes: str) -> str:
    listed = ", ".join(repr(prefix) for prefix in prefixes)
    return f"from next.seo import Rule\n\nrules = [Rule(disallow=[{listed}])]\n"


@pytest.fixture(autouse=True)
def _fresh_check_state() -> Iterator[None]:
    reset_check_caches()
    yield
    reset_check_caches()


def _ids(messages: list) -> list[str]:
    return [m.id for m in messages]


class TestRegistration:
    @pytest.mark.parametrize("check", CHECKS)
    def test_every_check_runs_by_default_under_the_urls_tag(self, check) -> None:
        register_all()
        assert set(check.tags) == {Tags.urls, NEXT}
        assert check in check_registry.registered_checks

    def test_a_tree_without_sources_is_silent(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", pages=("", "posts/[slug]"))):
            for check in CHECKS:
                assert check() == []

    def test_loaded_seo_roots_reads_every_tree_once(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root, root):
            init_errors, roots = loaded_seo_roots()
        assert init_errors == []
        assert [entry.path for entry in roots] == [root]
        assert set(roots[0].trails) == {"", "about"}
        assert roots[0].section == "pages"
        assert roots[0].robots is None
        assert roots[0].robots_file is None


class TestModuleImports:
    def test_a_sitemap_that_raises_is_reported_with_its_cause(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap='raise RuntimeError("boom")\n')
        with routed(root):
            messages = check_seo_module_imports()
        assert _ids(messages) == ["next.E110"]
        assert "RuntimeError: boom" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")

    def test_deferred_annotations_are_refused_in_both_modules(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=FUTURE_ITEMS,
            robots="from __future__ import annotations\n",
        )
        with routed(root):
            messages = check_seo_module_imports()
        assert _ids(messages) == ["next.E110", "next.E110"]
        assert [m.obj for m in messages] == [
            str(root / "sitemap.py"),
            str(root / "robots.py"),
        ]
        assert "__future__" in messages[0].msg

    def test_importing_modules_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=POSTS_ITEMS,
            robots=GOOD_ROBOTS,
        )
        with routed(root):
            assert check_seo_module_imports() == []


class TestItemsTrails:
    def test_an_unrouted_trail_is_an_error(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=UNKNOWN_ITEMS
        )
        with routed(root):
            messages = check_sitemap_items_trails()
        assert _ids(messages) == ["next.E111"]
        assert "@sitemap.items('nope/[slug]')" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")

    def test_a_routed_trail_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_items_trails() == []


class TestSitemapTemplates:
    def test_missing_templates_are_an_error_while_a_sitemap_exists(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root), override_settings(TEMPLATES=NO_APP_DIRS):
            messages = check_sitemap_templates()
        assert _ids(messages) == ["next.E112"]
        assert "sitemap.xml, sitemap_index.xml" in messages[0].msg
        assert "django.contrib.sitemaps" in messages[0].msg
        assert messages[0].obj is settings

    def test_the_templates_load_by_default(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap="")):
            assert check_sitemap_templates() == []

    def test_no_sitemap_asks_for_no_template(self, tmp_path) -> None:
        with (
            routed(write_tree(tmp_path / "pages")),
            override_settings(TEMPLATES=NO_APP_DIRS),
        ):
            assert check_sitemap_templates() == []


class TestModuleAttributes:
    def test_every_wrong_shape_is_named(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=BAD_SITEMAP, robots=BAD_ROBOTS)
        with routed(root):
            messages = check_seo_module_attributes()
        assert _ids(messages) == ["next.E113"] * 11
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

    def test_the_documented_shapes_pass(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=GOOD_SITEMAP, robots=GOOD_ROBOTS)
        with routed(root):
            assert check_seo_module_attributes() == []


class TestRobotsSingleSource:
    def test_both_forms_in_one_tree_are_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"User-agent: *\n")
        with routed(root):
            messages = check_robots_single_source()
        assert _ids(messages) == ["next.E114"]
        assert f"{root / 'robots.py'}, {root / 'robots.txt'}" in messages[0].msg
        assert messages[0].obj == str(root / "robots.py")

    def test_a_source_in_two_trees_is_an_error(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", robots_txt=b"User-agent: *\n")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second):
            messages = check_robots_single_source()
        assert _ids(messages) == ["next.E114"]
        assert f"only {first / 'robots.txt'} answers" in messages[0].msg

    def test_one_source_passes(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots="")):
            assert check_robots_single_source() == []


class TestRouteCollisions:
    def test_a_page_on_a_served_address_is_an_error(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("sitemap.xml", "sitemap-a.xml", "robots.txt"),
            sitemap="",
            robots="",
        )
        with routed(root):
            messages = check_seo_route_collisions()
        assert _ids(messages) == ["next.E115"] * 3
        assert {m.obj for m in messages} == {
            str(root / "sitemap.xml" / "page.py"),
            str(root / "sitemap-a.xml" / "page.py"),
            str(root / "robots.txt" / "page.py"),
        }

    def test_a_urlpattern_ahead_of_the_include_is_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=SHADOWED_URLCONF):
            messages = check_seo_route_collisions()
        assert _ids(messages) == ["next.E115", "next.E115"]
        assert "/sitemap.xml to tests.seo.shadowed_urls.mine" in messages[0].msg
        assert "/robots.txt to tests.seo.shadowed_urls.mine" in messages[1].msg
        assert all(m.obj is settings for m in messages)

    def test_a_urlpattern_behind_the_include_passes(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=USER_URLCONF):
            assert check_seo_route_collisions() == []

    def test_a_page_on_the_address_passes_without_a_source(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("sitemap.xml", "robots.txt"))
        with routed(root):
            assert check_seo_route_collisions() == []


class TestSectionLabels:
    def test_two_trees_with_one_label_are_an_error(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages", sitemap="")
        second = write_tree(tmp_path / "b" / "pages")
        with routed(first, second):
            messages = check_sitemap_section_labels()
        assert _ids(messages) == ["next.E116"]
        assert f"{first}, {second} share the sitemap section label 'pages'" in (
            messages[0].msg
        )
        assert messages[0].obj == str(second)

    def test_distinct_labels_pass(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", sitemap="")
        second = write_tree(tmp_path / "b", sitemap="")
        with routed(first, second):
            assert check_sitemap_section_labels() == []

    def test_labels_are_free_without_a_sitemap(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages")
        second = write_tree(tmp_path / "b" / "pages")
        with routed(first, second):
            assert check_sitemap_section_labels() == []


class TestRobotsFile:
    def test_a_file_that_is_not_utf8_is_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots_txt=b"User-agent: \xff\xfe\n")
        with routed(root):
            messages = check_robots_file()
        assert _ids(messages) == ["next.E117"]
        assert "does not decode as UTF-8" in messages[0].msg
        assert messages[0].obj == str(root / "robots.txt")

    def test_a_file_without_a_sitemap_line_warns_beside_a_sitemap(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=b"User-agent: *\n")
        with routed(root):
            messages = check_robots_file()
        assert _ids(messages) == ["next.W103"]
        assert "names no Sitemap: line" in messages[0].msg
        assert messages[0].obj == str(root / "robots.txt")

    def test_a_file_naming_the_sitemap_passes(self, tmp_path) -> None:
        content = b"User-agent: *\n\nsitemap: https://acme.example/sitemap.xml\n"
        with routed(write_tree(tmp_path / "pages", sitemap="", robots_txt=content)):
            assert check_robots_file() == []

    def test_a_file_needs_no_sitemap_line_without_a_sitemap(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots_txt=b"User-agent: *\n")):
            assert check_robots_file() == []


class TestDynamicRoutes:
    def test_an_unlisted_dynamic_route_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap="")
        with routed(root):
            messages = check_sitemap_dynamic_routes()
        assert _ids(messages) == ["next.W097"]
        assert "@sitemap.items('posts/[slug]')" in messages[0].msg
        assert messages[0].obj == str(root / "posts" / "[slug]" / "page.py")

    def test_a_listed_route_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_dynamic_routes() == []

    def test_an_excluded_route_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap='exclude = ["posts/*"]'
        )
        with routed(root):
            assert check_sitemap_dynamic_routes() == []


class TestNoindexItems:
    def test_items_on_a_noindex_page_warn(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=POSTS_ITEMS)
        page_path = write_page(root, "posts/[slug]", NOINDEX)
        with routed(root):
            messages = check_sitemap_noindex_items()
        assert _ids(messages) == ["next.W098"]
        assert "is noindex by its static metadata" in messages[0].msg
        assert messages[0].obj == str(page_path)

    def test_items_on_an_indexed_page_pass(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            assert check_sitemap_noindex_items() == []


class TestRoutesAtHostRoot:
    def test_routes_under_a_prefix_warn(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=PREFIX_ONLY_URLCONF):
            messages = check_seo_routes_at_host_root()
        assert _ids(messages) == ["next.W099", "next.W099"]
        assert messages[0].msg.startswith("/sitemap.xml does not resolve")
        assert messages[1].msg.startswith("/robots.txt does not resolve")
        assert "include('next.seo.urls')" in messages[0].msg
        assert all(m.obj is settings for m in messages)

    def test_a_host_root_mount_beside_the_prefix_passes(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=PREFIXED_URLCONF):
            assert check_seo_routes_at_host_root() == []

    def test_a_shadowing_pattern_is_not_a_missing_route(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=SHADOWED_URLCONF):
            assert check_seo_routes_at_host_root() == []


class TestRobotsDisallow:
    def test_a_disallow_over_the_sitemap_url_warns(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", sitemap="", robots=_disallow("/sitemap.xml")
        )
        with routed(root):
            messages = check_robots_disallow()
        assert _ids(messages) == ["next.W100"]
        assert "covers /sitemap.xml" in messages[0].msg
        assert messages[0].obj == str(root / "robots.py")

    def test_a_disallow_over_a_listed_route_warns(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "about", "posts/[slug]"),
            sitemap=POSTS_ITEMS,
            robots=_disallow("/about/", "/posts/", "/team/"),
        )
        with routed(root):
            messages = check_robots_disallow()
        assert _ids(messages) == ["next.W100", "next.W100"]
        assert "disallows '/about/', which covers /about/" in messages[0].msg
        assert "disallows '/posts/', which covers /posts/" in messages[1].msg

    def test_a_disallow_follows_the_mount_of_the_tree(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "posts/[slug]"),
            sitemap=POSTS_ITEMS,
            robots=_disallow("/posts/", "/prefix/posts/"),
        )
        with routed(root, urlconf=PREFIXED_URLCONF):
            messages = check_robots_disallow()
        assert _ids(messages) == ["next.W100"]
        assert "disallows '/prefix/posts/', which covers /prefix/posts/" in (
            messages[0].msg
        )

    def test_wildcards_and_anchors_read_like_a_crawler(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about", "about/team"),
            sitemap="",
            robots=_disallow("/*/team/", "/about/$"),
        )
        with routed(root):
            messages = check_robots_disallow()
        assert _ids(messages) == ["next.W100", "next.W100"]
        assert "covers /about/team/," in messages[0].msg
        assert "covers /about/," in messages[1].msg

    def test_a_disallow_over_a_noindex_page_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=_disallow("/hidden/"))
        write_page(root, "hidden", NOINDEX)
        with routed(root):
            messages = check_robots_disallow()
        assert _ids(messages) == ["next.W101"]
        assert "covers the noindex pages /hidden/" in messages[0].msg
        assert messages[0].obj == str(root / "robots.py")

    def test_a_disallow_over_an_excluded_or_indexed_page_passes(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "about", "private"),
            sitemap='exclude = ["private"]',
            robots=_disallow("/private/", "/team/"),
        )
        with routed(root):
            assert check_robots_disallow() == []


class TestSourcesBelowRoot:
    def test_a_source_below_the_top_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "blog"))
        (root / "blog" / "sitemap.py").write_text("")
        (root / "blog" / "robots.txt").write_bytes(b"")
        with routed(root):
            messages = check_seo_sources_below_root()
        assert _ids(messages) == ["next.W102", "next.W102"]
        assert [m.obj for m in messages] == [
            str(root / "blog" / "sitemap.py"),
            str(root / "blog" / "robots.txt"),
        ]
        assert f"Move it to {root / 'sitemap.py'}" in messages[0].msg

    def test_sources_at_the_top_pass(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=b"")
        with routed(root):
            assert check_seo_sources_below_root() == []


class TestResetCheckCaches:
    def test_the_seo_manager_and_the_items_registry_reset(self, tmp_path) -> None:
        def posts() -> list[dict[str, str]]:
            return []

        sitemap_items_registry.register(Path(tmp_path), "posts/[slug]", posts)
        version = seo_manager.version
        reset_check_caches()
        assert seo_manager.version != version
        assert sitemap_items_registry.entries_for(Path(tmp_path)) == ()
