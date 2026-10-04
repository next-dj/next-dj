from collections.abc import Iterator

import pytest
from django.contrib.sitemaps import Sitemap
from django.http import HttpRequest
from django.test import override_settings

from next.seo import SitemapBackend
from next.seo.checks import (
    check_seo_module_annotations,
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_sources_below_root,
    check_seo_sources_on_closed_site,
    check_sitemap_items_files,
)
from next.seo.registry import SitemapItemsEntry, sitemap_items_registry
from tests.seo.sources import listed_elsewhere
from tests.support import POSTS_ITEMS, check_ids, routed, write_tree


FUTURE_ITEMS = "from __future__ import annotations\n" + POSTS_ITEMS


BAD_SITEMAP = """
changefreq = "sometimes"
priority = 2
limit = 50001
cache = -1
exclude = "admin/**"
languages = ["en", 1]
i18n = "yes"
alternates = 1
x_default = None
protocol = "ftp"
section = "not a slug"
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
section = "blog"
"""


BAD_ROBOTS = """
rules = [1]
sitemaps = ["/sitemap.xml", "https://a.example/s.xml\\nUser-agent: evil"]
cache = True
"""


GOOD_ROBOTS = """
from next.seo import RobotsRule

rules = [RobotsRule(user_agent="*", disallow=["/private/"])]
sitemaps = ["https://cdn.example/news.xml"]
cache = 3600
"""


CALLABLE_ROBOTS = """
from django.http import HttpRequest

from next.seo import RobotsRule


def rules(request: HttpRequest) -> list[RobotsRule]:
    return [RobotsRule()]
"""


class TestModuleImports:
    """A source that fails to import is `next.E110`, deferred annotations `E119`."""

    def test_a_sitemap_that_raises_is_reported_with_its_cause(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap='raise RuntimeError("boom")\n')
        with routed(root):
            messages = check_seo_module_imports()
        assert check_ids(messages) == ["next.E110"]
        assert "RuntimeError: boom" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")

    def test_deferred_annotations_are_refused_in_both_resolved_sources(
        self, tmp_path
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=FUTURE_ITEMS,
            robots="from __future__ import annotations\n",
        )
        with routed(root):
            assert check_seo_module_imports() == []
            messages = check_seo_module_annotations()
        assert check_ids(messages) == ["next.E119", "next.E119"]
        assert [m.obj for m in messages] == [
            str(root / "sitemap.py"),
            str(root / "robots.py"),
        ]
        assert "__future__" in messages[0].msg

    def test_a_rule_refused_at_import_names_its_cause(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            robots="from next.seo import RobotsRule\n"
            "rules = [RobotsRule(disallow='/x\\nAllow: /')]\n",
        )
        with routed(root):
            [error] = check_seo_module_imports()
        assert error.id == "next.E110"
        assert "RobotsRuleError: RobotsRule.disallow is" in error.msg

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
        assert check_ids(messages) == ["next.E113"] * 13
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
            "section",
            "rules",
            "sitemaps",
            "cache",
        ]
        assert "one of always, daily" in messages[0].msg
        assert messages[0].obj == str(root / "sitemap.py")
        assert messages[-1].obj == str(root / "robots.py")

    def test_a_bool_cache_is_named(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = True\n")
        with routed(root):
            [error] = check_seo_module_attributes()
        assert error.id == "next.E113"
        assert (
            "cache = True, expected seconds as an int, False, or a valid" in error.msg
        )

    @pytest.mark.parametrize(
        "cache", ["False", "{'public': True, 's_maxage': 300, 'vary': ['Accept']}"]
    )
    def test_the_page_cache_forms_pass(self, tmp_path, cache) -> None:
        root = write_tree(
            tmp_path / "pages",
            sitemap=f"cache = {cache}\n",
            robots=f"cache = {cache}\n",
        )
        with routed(root):
            assert check_seo_module_attributes() == []

    @pytest.mark.parametrize(
        "cache",
        ["{'public': True, 'no_store': True}", "{'maxage': 60}", "lambda: 60"],
        ids=["contradiction", "unknown-key", "callable"],
    )
    def test_a_cache_the_route_cannot_read_is_e113(self, tmp_path, cache) -> None:
        root = write_tree(tmp_path / "pages", robots=f"cache = {cache}\n")
        with routed(root):
            [error] = check_seo_module_attributes()
        assert error.id == "next.E113"

    def test_two_items_callables_on_one_trail_are_e128(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=(
                "from next.seo import sitemap\n\n"
                "@sitemap.items('posts/[slug]')\n"
                "def drafts():\n    return []\n\n"
                "@sitemap.items('posts/[slug]')\n"
                "def published():\n    return []\n"
            ),
        )
        with routed(root):
            [error] = check_seo_module_attributes()
        assert error.id == "next.E128"
        assert "on drafts and then on published" in error.msg
        assert error.obj == str(root / "sitemap.py")

    def test_the_documented_shapes_pass(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap=GOOD_SITEMAP, robots=GOOD_ROBOTS)
        with routed(root):
            assert check_seo_module_attributes() == []

    def test_rules_may_be_a_callable(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=CALLABLE_ROBOTS)
        with routed(root):
            assert check_seo_module_attributes() == []


class TestSourcesBelowRoot:
    """A source below the top of its tree is never served (`next.W097`)."""

    def test_a_source_below_the_top_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "blog"))
        (root / "blog" / "sitemap.py").write_text("")
        (root / "blog" / "robots.txt").write_bytes(b"")
        with routed(root):
            messages = check_seo_sources_below_root()
        assert check_ids(messages) == ["next.W097"] * 2
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
        sitemap_items_registry.register(
            SitemapItemsEntry(nested, "blog", listed_elsewhere)
        )
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
            SitemapItemsEntry(root / "sitemap.py", "posts/[slug]", listed_elsewhere)
        )
        with routed(root):
            assert check_sitemap_items_files() == []

    def test_a_decorator_run_by_a_page_py_is_reported(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        sitemap_items_registry.register(
            SitemapItemsEntry(root / "page.py", "", listed_elsewhere)
        )
        with routed(root):
            [error] = check_sitemap_items_files()
        assert error.obj == str(root / "page.py")


def preview_hosts(request: HttpRequest | None) -> bool:
    return request is None


class ListedBackend(SitemapBackend):
    """A sitemap backend of the project, serving outside the page trees."""

    def sections(self, request: HttpRequest | None) -> dict[str, Sitemap]:
        """Answer one empty section."""
        return {"extra": Sitemap()}


@pytest.fixture()
def production() -> Iterator[None]:
    with override_settings(DEBUG=False):
        yield


@pytest.mark.usefixtures("production")
class TestSourcesOnAClosedSite:
    """`next.W111` warns when a site closed to search publishes for crawlers."""

    @pytest.mark.parametrize(
        ("sources", "named"),
        [
            ({"sitemap": ""}, "a sitemap"),
            ({"robots_txt": b"User-agent: *\n"}, "a robots.txt"),
            ({"robots": ""}, "a robots.txt"),
        ],
        ids=["sitemap", "robots-txt", "robots-py"],
    )
    def test_a_closed_site_publishing_for_crawlers_is_w111(
        self, tmp_path, sources: dict[str, object], named: str
    ) -> None:
        root = write_tree(tmp_path / "pages", **sources)
        with routed(root, SITE={"INDEXABLE": False}):
            messages = check_seo_sources_on_closed_site()
        assert check_ids(messages) == ["next.W111"]
        assert f"publishes {named} for crawlers" in messages[0].msg

    def test_a_sitemap_backend_of_its_own_is_a_source(self, tmp_path) -> None:
        backends = [{"BACKEND": "tests.seo.checks.test_sources.ListedBackend"}]
        with routed(
            write_tree(tmp_path / "pages"),
            SITE={"INDEXABLE": False},
            SEO={"SITEMAP_BACKENDS": backends},
        ):
            [message] = check_seo_sources_on_closed_site()
        assert "publishes a sitemap for crawlers" in message.msg

    def test_debug_does_not_silence_w111(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root, SITE={"INDEXABLE": False}), override_settings(DEBUG=True):
            assert check_ids(check_seo_sources_on_closed_site()) == ["next.W111"]

    def test_a_private_site_without_sources_is_silent(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages"), SITE={"INDEXABLE": False}):
            assert check_seo_sources_on_closed_site() == []

    @pytest.mark.parametrize(
        "site", [{}, {"INDEXABLE": True}, {"INDEXABLE": preview_hosts}]
    )
    def test_an_open_or_host_bound_site_is_silent(
        self, tmp_path, site: dict[str, object]
    ) -> None:
        with routed(write_tree(tmp_path / "pages", sitemap=""), SITE=site):
            assert check_seo_sources_on_closed_site() == []
