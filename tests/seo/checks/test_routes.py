import pytest
from django.conf import settings

from next.seo.checks import check_seo_route_collisions, check_seo_routes_at_host_root
from tests.seo.trees import PREFIXED_URLCONF, USER_URLCONF, routed, write_tree
from tests.support import check_ids


SHADOWED_URLCONF = "tests.seo.urls_shadowed"


PREFIX_ONLY_URLCONF = "tests.seo.urls_prefix_only"


class TestRouteCollisions:
    """A page or a urlpattern on a served SEO address is an error (`next.E115`)."""

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

    def test_a_urlpattern_ahead_of_the_include_is_an_error(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=SHADOWED_URLCONF):
            messages = check_seo_route_collisions()
        assert check_ids(messages) == ["next.E115", "next.E115"]
        assert "/sitemap.xml to tests.seo.urls_shadowed.mine" in messages[0].msg
        assert "/robots.txt to tests.seo.urls_shadowed.mine" in messages[1].msg
        assert all(m.obj is settings for m in messages)

    def test_a_urlpattern_behind_the_include_passes(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=USER_URLCONF):
            assert check_seo_route_collisions() == []

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
    """The SEO routes resolve at the host root or warn (`next.W099`)."""

    def test_routes_under_a_prefix_warn(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=PREFIX_ONLY_URLCONF):
            messages = check_seo_routes_at_host_root()
        assert check_ids(messages) == ["next.W099", "next.W099"]
        assert messages[0].msg.startswith("/sitemap.xml does not resolve")
        assert messages[1].msg.startswith("/robots.txt does not resolve")
        assert "include('next.seo.urls')" in messages[0].msg
        assert all(m.obj is settings for m in messages)

    @pytest.mark.parametrize(
        "urlconf",
        [PREFIXED_URLCONF, SHADOWED_URLCONF],
        ids=["host_root_mount", "shadowing_pattern"],
    )
    def test_a_route_that_resolves_at_the_host_root_passes(
        self, tmp_path, urlconf: str
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=urlconf):
            assert check_seo_routes_at_host_root() == []
