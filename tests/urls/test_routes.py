from pathlib import Path

from django.urls import reverse

from next.intercept import Intercept, intercepts
from next.urls.routes import Route, collect_routes, with_intercepts
from tests.support import (
    ENTRY_ROUTER,
    installed_apps,
    routers_from,
    write_app,
    write_page,
    write_urlconf,
)
from tests.support.access import PAGE_HEAD
from tests.support.cases.intercepts import zone_body


def _rows(root: Path) -> list[tuple[str, str, str]]:
    return [
        (route.url, route.name, str(route.file.relative_to(root)))
        for route in collect_routes()
    ]


def _project(root: Path, *entries: str) -> str:
    write_app(root, "wallets", {"pages": ("ops/[int:pk]",)})
    write_app(root, "blog", {"pages": ("", "about")})
    write_app(root, "audit", {"pages": ("log",)})
    return write_urlconf(root, "rows_urls", *entries)


class TestCollectRoutes:
    """One row per page in resolution order, from the URLconf root."""

    def test_every_tree_around_the_framework_include(self, tmp_path) -> None:
        urlconf = _project(
            tmp_path,
            'path("org/<int:org>/audit/", pages("audit"))',
            'path("app/", include("next.urls"))',
            'path("crew/", pages("blog", namespace="crew"))',
        )

        with installed_apps(tmp_path, "wallets", "blog", "audit", urlconf=urlconf):
            rows = _rows(tmp_path)

        assert rows == [
            ("/org/<int:org>/audit/log/", "audit:page_log", "audit/pages/log/page.py"),
            (
                "/app/ops/<int:pk>/",
                "next:page_ops_int_pk",
                "wallets/pages/ops/[int:pk]/page.py",
            ),
            ("/crew/", "crew:page_", "blog/pages/page.py"),
            ("/crew/about/", "crew:page_about", "blog/pages/about/page.py"),
        ]

    def test_a_tree_inside_a_namespaced_include_reverses_by_the_full_name(
        self, tmp_path
    ) -> None:
        urlconf = _project(
            tmp_path,
            'path("o/", include(([path("blog/", pages("blog"))], "outer"), '
            'namespace="outer"))',
        )

        with installed_apps(tmp_path, "wallets", "blog", "audit", urlconf=urlconf):
            rows = _rows(tmp_path)
            reversed_about = reverse("outer:blog:page_about")

        assert (
            "/o/blog/about/",
            "outer:blog:page_about",
            "blog/pages/about/page.py",
        ) in rows
        assert reversed_about == "/o/blog/about/"

    def test_auto_trees_are_listed_only_where_the_framework_is_included(
        self, tmp_path
    ) -> None:
        urlconf = _project(tmp_path, 'path("audit/", pages("audit"))')

        with installed_apps(tmp_path, "wallets", "blog", "audit", urlconf=urlconf):
            rows = _rows(tmp_path)

        assert [url for url, _name, _file in rows] == ["/audit/log/"]

    def test_a_project_root_tree_and_a_virtual_page(self, tmp_path) -> None:
        site = tmp_path / "site"
        write_page(site, "contact")
        (site / "about").mkdir()
        (site / "about" / "template.djx").write_text("<p>about</p>\n")

        with installed_apps(tmp_path, dirs=[site]):
            routes = collect_routes()

        assert routes == [
            Route(
                url="/contact/",
                name="next:page_contact",
                file=site.resolve() / "contact" / "page.py",
            ),
            Route(
                url="/about/",
                name="next:page_about",
                file=site.resolve() / "about" / "template.djx",
            ),
        ]

    def test_a_page_whose_trail_does_not_parse_is_left_out(self, tmp_path) -> None:
        write_app(tmp_path, "shop", {"pages": ("cart", "item/[id]/[id]")})

        with installed_apps(tmp_path, "shop"):
            rows = _rows(tmp_path)

        assert [url for url, *_rest in rows] == ["/cart/"]

    def test_without_a_file_router_there_are_no_routes(self) -> None:
        with routers_from(ENTRY_ROUTER):
            routes = collect_routes()

        assert routes == []


class TestOnlyRoutedPages:
    """The table lists the pages the router routes and nothing else on disk."""

    def test_a_page_without_a_body_source_is_left_out(self, tmp_path) -> None:
        write_app(tmp_path, "blog", {"pages": ("", "about")})
        write_page(tmp_path / "blog" / "pages", "draft", "title = 'no body'\n")

        with installed_apps(tmp_path, "blog"):
            rows = [route.url for route in collect_routes()]

        assert rows == ["/", "/about/"]

    def test_a_page_the_parser_refuses_is_left_out(self, tmp_path) -> None:
        write_app(tmp_path, "blog", {"pages": ("", "twice/[x]/[x]")})

        with installed_apps(tmp_path, "blog"):
            rows = [route.url for route in collect_routes()]

        assert rows == ["/"]


class TestRouteGroups:
    """The table lists a grouped page at its group-free URL and name."""

    def test_the_row_leaves_the_group_out_and_the_file_keeps_it(self, tmp_path) -> None:
        site = tmp_path / "site"
        page_file = write_page(site, "(auth)/login")

        with installed_apps(tmp_path, dirs=[site]):
            routes = collect_routes()

        assert routes == [
            Route(url="/login/", name="next:page_login", file=page_file.resolve())
        ]


def _intercept_site(root: Path) -> tuple[Path, Path]:
    target = write_page(root, "feed/photo")
    rule = write_page(root, "feed/(.)photo", PAGE_HEAD, body=zone_body("peek"))
    return target, rule


class TestWithIntercepts:
    """An intercept row follows the row of its target and carries its rule."""

    def test_the_row_follows_its_target(self, tmp_path) -> None:
        site = tmp_path / "site"
        target, rule = _intercept_site(site)
        write_page(site, "about")

        with installed_apps(tmp_path, dirs=[site]):
            routes = with_intercepts(collect_routes())

        assert [(route.url, route.name, route.file) for route in routes] == [
            ("/about/", "next:page_about", site.resolve() / "about" / "page.py"),
            ("/feed/photo/", "next:page_feed_photo", target.resolve()),
            ("/feed/photo/", "", rule.resolve()),
        ]
        assert routes[2].intercept == Intercept(
            target.resolve(), rule.resolve(), rule.parent.resolve(), "(.)"
        )
        assert routes[1].intercept is None

    def test_a_virtual_intercept_is_listed_by_its_template(self, tmp_path) -> None:
        site = tmp_path / "site"
        target = write_page(site, "feed/photo")
        directory = site / "feed" / "(.)photo"
        directory.mkdir(parents=True)
        (directory / "template.djx").write_text(zone_body("peek"))

        with installed_apps(tmp_path, dirs=[site]):
            routes = with_intercepts(collect_routes())

        assert [route.file for route in routes] == [
            target.resolve(),
            directory.resolve() / "template.djx",
        ]

    def test_rules_over_one_target_follow_in_file_order(self, tmp_path) -> None:
        site = tmp_path / "site"
        write_page(site, "feed/photo")
        near = write_page(site, "feed/(.)photo", PAGE_HEAD, body=zone_body("a"))
        far = write_page(site, "(...)feed/photo", PAGE_HEAD, body=zone_body("b"))

        with installed_apps(tmp_path, dirs=[site]):
            routes = with_intercepts(collect_routes())

        assert [route.file for route in routes[1:]] == [far.resolve(), near.resolve()]

    def test_a_tree_without_intercepts_comes_back_unchanged(self, tmp_path) -> None:
        site = tmp_path / "site"
        write_page(site, "feed/photo")

        with installed_apps(tmp_path, dirs=[site]):
            routes = collect_routes()

        assert with_intercepts(routes) == routes

    def test_a_tree_mounted_twice_lists_the_intercept_under_both_urls(
        self, tmp_path
    ) -> None:
        write_app(tmp_path, "blog", {"pages": ("feed/photo",)})
        write_page(
            tmp_path / "blog" / "pages",
            "feed/(.)photo",
            PAGE_HEAD,
            body=zone_body("peek"),
        )
        urlconf = write_urlconf(
            tmp_path,
            "twice_urls",
            'path("a/", pages("blog", namespace="a"))',
            'path("b/", pages("blog", namespace="b"))',
        )

        with installed_apps(tmp_path, "blog", urlconf=urlconf):
            routes = with_intercepts(collect_routes())

        assert [(route.url, route.intercept is not None) for route in routes] == [
            ("/a/feed/photo/", False),
            ("/a/feed/photo/", True),
            ("/b/feed/photo/", False),
            ("/b/feed/photo/", True),
        ]

    def test_a_cleared_table_lists_no_intercept(self, tmp_path) -> None:
        site = tmp_path / "site"
        _intercept_site(site)

        with installed_apps(tmp_path, dirs=[site]):
            routes = collect_routes()
            intercepts.clear()

            assert [route.intercept for route in with_intercepts(routes)] == [None]
