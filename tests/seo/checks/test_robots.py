from pathlib import Path
from unittest.mock import patch

import pytest
from django.urls import NoReverseMatch

from next.seo.checks import (
    check_robots_disallow,
    check_robots_file,
    check_robots_single_source,
)
from next.seo.checks.robots import route_paths
from next.seo.checks.roots import loaded_seo_roots
from tests.seo.trees import (
    NAMESPACED_URLCONF,
    NOINDEX,
    POSTS_ITEMS,
    PREFIXED_URLCONF,
    routed,
    write_tree,
)
from tests.support import check_ids, write_page


def _disallow(*prefixes: str) -> str:
    listed = ", ".join(repr(prefix) for prefix in prefixes)
    return f"from next.seo import Rule\n\nrules = [Rule(disallow=[{listed}])]\n"


class TestRobotsSingleSource:
    """`/robots.txt` takes one source across every tree (`next.E114`)."""

    def test_both_forms_in_one_tree_are_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"User-agent: *\n")
        with routed(root):
            messages = check_robots_single_source()
        assert check_ids(messages) == ["next.E114"]
        assert f"{root / 'robots.py'}, {root / 'robots.txt'}" in messages[0].msg
        assert messages[0].obj == str(root / "robots.py")

    def test_a_source_in_two_trees_is_an_error(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", robots_txt=b"User-agent: *\n")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second):
            messages = check_robots_single_source()
        assert check_ids(messages) == ["next.E114"]
        assert f"only {first / 'robots.txt'} answers" in messages[0].msg

    def test_one_source_passes(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots="")):
            assert check_robots_single_source() == []


class TestRobotsFile:
    """A static `robots.txt` decodes as UTF-8 and names a served sitemap."""

    def test_a_file_that_is_not_utf8_is_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots_txt=b"User-agent: \xff\xfe\n")
        with routed(root):
            messages = check_robots_file()
        assert check_ids(messages) == ["next.E117"]
        assert "does not decode as UTF-8" in messages[0].msg
        assert messages[0].obj == str(root / "robots.txt")

    def test_a_file_without_a_sitemap_line_warns_beside_a_sitemap(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=b"User-agent: *\n")
        with routed(root):
            messages = check_robots_file()
        assert check_ids(messages) == ["next.W103"]
        assert "names no Sitemap: line" in messages[0].msg
        assert messages[0].obj == str(root / "robots.txt")

    @pytest.mark.parametrize(
        ("sitemap", "content"),
        [
            ("", b"User-agent: *\n\nsitemap: https://acme.example/sitemap.xml\n"),
            (None, b"User-agent: *\n"),
        ],
        ids=["names_the_sitemap", "no_sitemap"],
    )
    def test_a_file_naming_every_served_sitemap_passes(
        self, tmp_path, sitemap: str | None, content: bytes
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap=sitemap, robots_txt=content)
        with routed(root):
            assert check_robots_file() == []


class TestRobotsDisallow:
    """A disallow over a listed or noindex route warns, read like a crawler."""

    def test_a_disallow_over_the_sitemap_url_warns(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", sitemap="", robots=_disallow("/sitemap.xml")
        )
        with routed(root):
            messages = check_robots_disallow()
        assert check_ids(messages) == ["next.W100"]
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
        assert check_ids(messages) == ["next.W100", "next.W100"]
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
        assert check_ids(messages) == ["next.W100"]
        assert "disallows '/prefix/posts/', which covers /prefix/posts/" in (
            messages[0].msg
        )

    @pytest.mark.parametrize(
        ("urlconf", "prefix"),
        [(None, "/posts/"), (PREFIXED_URLCONF, "/prefix/posts/")],
        ids=["host_root", "prefixed"],
    )
    def test_a_tree_of_dynamic_routes_alone_is_read(
        self, tmp_path, urlconf: str | None, prefix: str
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=POSTS_ITEMS,
            robots=_disallow(prefix),
        )
        with routed(root, **({} if urlconf is None else {"urlconf": urlconf})):
            messages = check_robots_disallow()
        assert check_ids(messages) == ["next.W100"]
        assert f"disallows {prefix!r}, which covers {prefix}" in messages[0].msg

    def test_a_disallow_over_a_dynamic_noindex_page_alone_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=(), robots=_disallow("/hidden/"))
        write_page(root, "hidden/[int:id]", NOINDEX)
        with routed(root):
            messages = check_robots_disallow()
        assert check_ids(messages) == ["next.W101"]
        assert "covers the noindex pages /hidden/" in messages[0].msg

    def test_wildcards_and_anchors_read_like_a_crawler(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about", "about/team"),
            sitemap="",
            robots=_disallow("/*/team/", "/about/$"),
        )
        with routed(root):
            messages = check_robots_disallow()
        assert check_ids(messages) == ["next.W100", "next.W100"]
        assert "covers /about/team/," in messages[0].msg
        assert "covers /about/," in messages[1].msg

    def test_a_disallow_over_a_noindex_page_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=_disallow("/hidden/"))
        write_page(root, "hidden", NOINDEX)
        with routed(root):
            messages = check_robots_disallow()
        assert check_ids(messages) == ["next.W101"]
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


class _Refusing:
    """Match any text but refuse every value, the way a strict converter does."""

    regex = ".+"

    def to_python(self, value: str) -> str:
        raise ValueError(value)


class _Capitals:
    """Take capitals only, which no placeholder spells."""

    regex = "[A-Z]+"


def _paths(
    root: Path, *pages: str, urlconf: str = NAMESPACED_URLCONF
) -> dict[str, str]:
    """Return the URL paths `route_paths` reads for a tree of `pages` at `root`."""
    write_tree(root, pages=pages)
    with routed(root, urlconf=urlconf):
        _errors, roots = loaded_seo_roots()
        return route_paths(roots[0])


class TestRoutePaths:
    """Every trail reverses on its own, a dynamic one cut at its first parameter."""

    def test_dynamic_routes_alone_are_placed(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path, "posts/[slug]", "archive/[int:year]/[slug]")
        assert paths == {
            "posts/[slug]": "/posts/",
            "archive/[int:year]/[slug]": "/archive/",
        }

    def test_a_uuid_route_is_placed(self, tmp_path: Path) -> None:
        assert _paths(tmp_path, "items/[uuid:key]") == {"items/[uuid:key]": "/items/"}

    def test_the_mount_of_the_include_is_kept(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path, "", "posts/[slug]", urlconf=PREFIXED_URLCONF)
        assert paths == {"": "/prefix/", "posts/[slug]": "/prefix/posts/"}

    @pytest.mark.parametrize(
        "converter",
        [None, _Capitals(), _Refusing()],
        ids=["unknown", "unspellable", "refusing"],
    )
    def test_a_parameter_no_placeholder_fills_skips_the_trail(
        self, tmp_path: Path, converter: object
    ) -> None:
        converters = {} if converter is None else {"str": converter, "int": converter}
        with patch("next.seo.checks.robots.get_converters", return_value=converters):
            paths = _paths(tmp_path, "about", "posts/[slug]", "items/[int:id]")
        assert paths == {"about": "/about/"}

    def test_a_trail_that_does_not_reverse_is_skipped(self, tmp_path: Path) -> None:
        with patch(
            "next.seo.checks.robots.page_reverse", side_effect=NoReverseMatch("x")
        ):
            assert _paths(tmp_path, "about") == {}

    def test_a_reverse_under_another_route_is_skipped(self, tmp_path: Path) -> None:
        with patch("next.seo.checks.robots.page_reverse", return_value="/elsewhere/"):
            assert _paths(tmp_path, "about") == {}
