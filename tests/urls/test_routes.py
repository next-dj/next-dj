from pathlib import Path

from django.urls import reverse

from next.urls.routes import Route, collect_routes
from tests.support import (
    ENTRY_ROUTER,
    installed_apps,
    routers_from,
    write_app,
    write_page,
    write_urlconf,
)


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
