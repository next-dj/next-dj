import pytest
from django.conf import settings

from next.seo.checks import check_seo_route_collisions, check_seo_routes_at_host_root
from tests.support import (
    PREFIX_ONLY_URLCONF,
    PREFIXED_URLCONF,
    SHADOWED_URLCONF,
    USER_URLCONF,
    check_ids,
    routed,
    write_tree,
)


class TestRouteCollisions:
    """A page on a served SEO address is an error (`next.E115`)."""

    def test_a_page_on_a_served_address_is_an_error(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("sitemap.xml", "sitemap-a.xml", "robots.txt"),
            sitemap="",
            robots="",
        )
        with routed(root):
            messages = check_seo_route_collisions()
        assert check_ids(messages) == ["next.E115"] * 3
        assert {m.obj for m in messages} == {
            str(root / "sitemap.xml" / "page.py"),
            str(root / "sitemap-a.xml" / "page.py"),
            str(root / "robots.txt" / "page.py"),
        }

    def test_the_message_names_the_page_path_and_the_served_address(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("sitemap.xml",), sitemap="")
        with routed(root):
            [message] = check_seo_route_collisions()
        assert message.msg == (
            f"{root / 'sitemap.xml' / 'page.py'} routes /sitemap.xml/, beside the "
            "/sitemap.xml the framework serves from the SEO sources, so crawlers and "
            "visitors reach two different documents. Rename the directory."
        )

    def test_a_nested_trail_takes_no_section_address(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("sitemap-a/b.xml", "sitemap-.xml"), sitemap=""
        )
        with routed(root):
            assert check_seo_route_collisions() == []

    def test_a_page_on_the_address_passes_without_a_source(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("sitemap.xml", "robots.txt"))
        with routed(root):
            assert check_seo_route_collisions() == []


class TestRoutesAtHostRoot:
    """The SEO routes resolve to the framework at the host root or warn (W094)."""

    def test_routes_under_a_prefix_warn(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=PREFIX_ONLY_URLCONF):
            messages = check_seo_routes_at_host_root()
        assert check_ids(messages) == ["next.W094", "next.W094"]
        assert messages[0].msg.startswith("/sitemap.xml does not resolve")
        assert messages[1].msg.startswith("/robots.txt does not resolve")
        assert "include('next.seo.urls')" in messages[0].msg
        assert all(m.obj is settings for m in messages)

    def test_a_urlpattern_ahead_of_the_framework_warns(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=SHADOWED_URLCONF):
            messages = check_seo_routes_at_host_root()
        assert check_ids(messages) == ["next.W094", "next.W094"]
        assert "/sitemap.xml resolves to tests.support.sites.mine" in messages[0].msg
        assert "/robots.txt resolves to tests.support.sites.mine" in messages[1].msg

    @pytest.mark.parametrize(
        "urlconf",
        [None, PREFIXED_URLCONF, USER_URLCONF],
        ids=["next_urls_at_root", "host_root_mount", "pattern_behind_the_include"],
    )
    def test_the_framework_route_at_the_host_root_passes(
        self, tmp_path, urlconf: str | None
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, **({} if urlconf is None else {"urlconf": urlconf})):
            assert check_seo_routes_at_host_root() == []
