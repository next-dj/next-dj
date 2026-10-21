from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import URLResolver, reverse

from next.urls import page_reverse, pages, router_manager
from next.urls.backends import _installed_app_directories
from next.urls.manager import _RoutedPages, routed_pages
from next.urls.mount import MountedTree
from next.utils import PageRoot
from tests.support import (
    ENTRY_ROUTER,
    file_router,
    file_router_config_entry,
    installed_apps,
    routers_from,
    write_app,
    write_page,
    write_urlconf,
)


def _ids_page(outer: str, inner: str) -> str:
    return (
        "from next.pages import context\n"
        "from next.urls import DUrl\n\n"
        'template = "{{ outer }}-{{ inner }}"\n\n\n'
        "@context\n"
        f"def ids({outer}: DUrl[int], {inner}: DUrl[int]):\n"
        f'    return {{"outer": {outer}, "inner": {inner}}}\n'
    )


def _routed() -> list[tuple[str, str]]:
    return [(str(pattern.pattern), pattern.name) for pattern in router_manager]


def _roots() -> list[PageRoot]:
    return [root for router in router_manager.backends for root in router.page_roots()]


def _status(*urls: str) -> list[int]:
    client = Client()
    return [client.get(url).status_code for url in urls]


class TestAutoAssembledAppTrees:
    """An app tree no `pages()` claims routes at the root, ahead of the root trees."""

    def test_app_trees_lead_then_root_trees(self, tmp_path) -> None:
        write_app(tmp_path, "blog", {"pages": ("about",)})
        write_app(tmp_path, "corp.billing", {"pages": ("pay",)})
        write_page(tmp_path / "site", "contact")

        with installed_apps(tmp_path, "blog", "corp.billing", dirs=[tmp_path / "site"]):
            routed = _routed()
            roots = _roots()

        assert [route for route, _name in routed] == ["about/", "pay/", "contact/"]
        assert [(root.label, root.prefix, root.namespace) for root in roots] == [
            ("App 'blog'", None, "next"),
            ("App 'corp.billing'", None, "next"),
            ("Root", None, "next"),
        ]

    def test_a_plain_directory_map_routes_the_app_tree(self, tmp_path) -> None:
        write_page(tmp_path / "pages", "pay")
        router = file_router(app_dirs=True)

        with patch(
            "next.urls.backends._installed_app_directories",
            return_value={"corp.billing": tmp_path},
        ):
            (root,) = router.page_roots()
            routed = [str(pattern.pattern) for pattern in router.generate_urls()]

        assert (root.label, root.prefix) == ("App 'corp.billing'", None)
        assert routed == ["pay/"]


class TestUnprefixedProjects:
    """A project declaring no prefix routes exactly the patterns it always did."""

    def _project(self, root: Path) -> list[str]:
        write_app(root, "blog", {"pages": ("", "about", "posts/[slug]")})
        write_app(root, "shop", {"pages": ("cart", "items/[int:id]")})
        write_page(root / "site", "contact")
        return ["blog", "shop"]

    def test_patterns_names_and_order(self, tmp_path) -> None:
        names = self._project(tmp_path)

        with installed_apps(tmp_path, *names, dirs=[tmp_path / "site"]):
            routed = _routed()
            roots = _roots()

        assert routed == [
            ("", "page_"),
            ("about/", "page_about"),
            ("posts/<str:slug>/", "page_posts_slug"),
            ("items/<int:id>/", "page_items_int_id"),
            ("cart/", "page_cart"),
            ("contact/", "page_contact"),
        ]
        assert [(root.prefix, root.namespace) for root in roots] == [
            (None, "next"),
            (None, "next"),
            (None, "next"),
        ]

    def test_an_auto_assembled_view_carries_its_trail(self, tmp_path) -> None:
        names = self._project(tmp_path)

        with installed_apps(tmp_path, *names):
            views = [pattern.callback for pattern in router_manager]

        assert all(isinstance(view.next_route_trail, str) for view in views)


class TestInstalledAppsSnapshot:
    """The app directories are read once per app registry snapshot, not per call."""

    def test_one_snapshot_serves_every_read(self, tmp_path) -> None:
        write_app(tmp_path, "shop")

        with installed_apps(tmp_path, "shop"):
            first = _installed_app_directories()
            _roots()
            _routed()
            second = _installed_app_directories()

        assert first is second

    def test_installing_an_app_takes_a_new_snapshot(self, tmp_path) -> None:
        write_app(tmp_path, "shop")
        before = _installed_app_directories()

        with installed_apps(tmp_path, "shop"):
            during = _installed_app_directories()

        assert during is not before
        assert "shop" in during
        assert "shop" not in before


class TestPrefixKwargsReachThePage:
    """A kwarg the prefix captures reaches `DUrl` on a page deep inside the tree."""

    def test_a_pages_include_prefix(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ()})
        write_page(
            tmp_path / "audit" / "pages", "log/[int:entry]", _ids_page("org", "entry")
        )
        urlconf = write_urlconf(
            tmp_path, "kwargs_urls", 'path("org/<int:org>/audit/", pages("audit"))'
        )

        with installed_apps(tmp_path, "audit", urlconf=urlconf):
            response = Client().get("/org/7/audit/log/2/")

        assert response.status_code == 200
        assert response.content.decode().strip() == "7-2"


_HOST_PAGE = (
    "from next.pages import context\n\n"
    'template = "host"\n\n\n'
    '@context("inherited", inherit_context=True)\n'
    "def inherited():\n"
    '    return "from-host"\n'
)

_GUEST_PAGE = "template = \"guest={{ inherited|default:'none' }}\"\n"


class TestTreesUnderAHostPrefix:
    """A tree `pages()` mounts under a prefix of another tree takes nothing from it."""

    def _bodies(self, root: Path, *urls: str) -> list[str]:
        write_app(root, "hub", {})
        hub = root / "hub" / "pages"
        for trail in ("", "staff"):
            write_page(hub, trail, _HOST_PAGE)
            (hub / trail / "layout.djx").write_text(
                f"<{trail or 'root'}>{{% template %}}"
            )
        write_page(hub, "staff/team", _GUEST_PAGE)
        write_app(root, "audit", {})
        write_page(root / "audit" / "pages", "log", _GUEST_PAGE)
        urlconf = write_urlconf(
            root,
            "host_prefix_urls",
            'path("staff/audit/", pages("audit"))',
            'path("", include("next.urls"))',
        )
        client = Client()
        with installed_apps(root, "hub", "audit", urlconf=urlconf):
            responses = [client.get(url) for url in urls]
        assert [response.status_code for response in responses] == [200] * len(urls)
        return [response.content.decode().strip() for response in responses]

    def test_layouts_and_inherited_context_stop_at_the_tree(self, tmp_path) -> None:
        own, mounted = self._bodies(tmp_path, "/staff/team/", "/staff/audit/log/")

        assert "guest=from-host" in own
        assert own.startswith("<root><staff>")
        assert mounted == "guest=none"


class TestReverse:
    """A `pages()` tree reverses by its own trails in its namespace."""

    def test_a_pages_tree_in_its_namespace(self, tmp_path) -> None:
        write_app(
            tmp_path, "wallets", {"pages": ("", "ops"), "staff_pages": ("", "team")}
        )
        urlconf = write_urlconf(
            tmp_path,
            "reverse_urls",
            'path("org/<int:org>/w/", pages("wallets"))',
            'path("crew/", pages("wallets", "staff_pages", namespace="crew"))',
        )

        with installed_apps(tmp_path, "wallets", urlconf=urlconf):
            urls = [
                page_reverse("ops", namespace="wallets", org=1),
                page_reverse(namespace="wallets", org=1),
                page_reverse("team", namespace="crew"),
                reverse("crew:page_"),
            ]

        assert urls == ["/org/1/w/ops/", "/org/1/w/", "/crew/team/", "/crew/"]


class TestPagesInTheUrlconf:
    """`pages()` mounts a tree where a URLconf includes it, out of the auto-assembly."""

    def test_a_claimed_subdirectory_leaves_the_pages_tree_routed(
        self, tmp_path
    ) -> None:
        write_app(tmp_path, "wallets", {"pages": ("ops",), "staff_pages": ("team",)})
        urlconf = write_urlconf(
            tmp_path,
            "claim_urls",
            'path("", include("next.urls"))',
            'path("crew/", pages("wallets", "staff_pages", namespace="crew"))',
        )

        with installed_apps(tmp_path, "wallets", urlconf=urlconf):
            routed = _routed()
            roots = _roots()
            statuses = _status("/crew/team/", "/team/", "/ops/")

        assert routed == [("ops/", "page_ops")]
        assert [(root.label, root.prefix, root.namespace) for root in roots] == [
            ("App 'wallets'", None, "next"),
            ("App 'wallets' (staff_pages)", "crew/", "crew"),
        ]
        assert statuses == [200, 404, 200]

    def test_an_undeclared_tree_leaves_the_auto_assembly(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("log",)})
        write_app(tmp_path, "blog", {"pages": ("about",)})
        urlconf = write_urlconf(
            tmp_path,
            "claimed_urls",
            'path("audit/", pages("audit"))',
            'path("", include("next.urls"))',
        )

        with installed_apps(tmp_path, "audit", "blog", urlconf=urlconf):
            routed = _routed()
            statuses = _status("/audit/log/", "/log/", "/about/")

        assert routed == [("about/", "page_about")]
        assert statuses == [200, 404, 200]

    def test_the_tree_names_its_pages_without_the_outer_prefix(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("", "log/[int:entry]")})

        with installed_apps(tmp_path, "audit"):
            tree, app_name, namespace = pages("audit")
            inner = tree[0].url_patterns
            names = [(str(pattern.pattern), pattern.name) for pattern in inner]

        assert (app_name, namespace) == ("audit", "audit")
        assert names == [("", "page_"), ("log/<int:entry>/", "page_log_int_entry")]

    @pytest.mark.parametrize(
        ("app", "subdir", "namespace", "expected"),
        [
            ("billing", "pages", None, ("billing", "billing", "pages")),
            ("corp.billing", "pages", None, ("billing", "billing", "pages")),
            ("billing", "admin_pages", "staff", ("billing", "staff", "admin_pages")),
        ],
        ids=["label", "dotted_name", "subdir_and_namespace"],
    )
    def test_the_include_triple(self, tmp_path, app, subdir, namespace, expected):
        write_app(tmp_path, "corp.billing", {"pages": ("",), "admin_pages": ("",)})

        with installed_apps(tmp_path, "corp.billing"):
            tree, app_name, chosen = pages(app, subdir, namespace=namespace)

        assert isinstance(tree, MountedTree)
        assert (app_name, chosen, tree.mount.subdir) == expected
        assert tree.mount.name == "corp.billing"
        assert tree.mount.path == tmp_path / "corp" / "billing" / expected[2]

    def test_an_unknown_app_is_a_lookup_error(self, tmp_path) -> None:
        with pytest.raises(LookupError, match="'ghost'"):
            pages("ghost")

    def test_a_missing_subdirectory_is_improperly_configured(self, tmp_path) -> None:
        write_app(tmp_path, "audit")

        with (
            installed_apps(tmp_path, "audit"),
            pytest.raises(ImproperlyConfigured, match="'audit', 'gone'"),
        ):
            pages("audit", "gone")

    def test_the_urlconf_import_fails_like_a_missing_include(self, tmp_path) -> None:
        urlconf = write_urlconf(tmp_path, "ghost_urls", 'path("x/", pages("ghost"))')

        with (
            installed_apps(tmp_path, urlconf=urlconf),
            pytest.raises(LookupError, match="'ghost'"),
        ):
            Client().get("/x/")


class TestMountedTree:
    """The include target of `pages()` builds its resolver on first read alone."""

    def test_the_repr_builds_nothing(self, tmp_path) -> None:
        write_app(tmp_path, "audit")

        with (
            installed_apps(tmp_path, "audit"),
            patch("next.urls.mount.build_url_resolver") as build,
        ):
            tree, _app, _namespace = pages("audit")
            text = repr(tree)

        assert text == "<MountedTree audit/pages>"
        build.assert_not_called()

    def test_one_resolver_per_router_version(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("", "log")})

        with installed_apps(tmp_path, "audit"):
            tree, _app, _namespace = pages("audit")
            first = tree[0]
            again = list(tree)
            sliced = tree[0:1]
            size = len(tree)
            router_manager.reload()
            rebuilt = tree[0]

        assert isinstance(first, URLResolver)
        assert again == sliced == [first]
        assert size == 1
        assert rebuilt is not first

    def test_the_tree_patterns_are_a_lazy_sequence(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("", "log")})

        with installed_apps(tmp_path, "audit"):
            tree, _app, _namespace = pages("audit")
            inner = tree[0].url_patterns
            patterns = list(inner)
            reads = (len(inner), list(reversed(inner)), inner[1], inner[:1])
            token = inner.version_token()
            version = router_manager.version
            built_once = next(iter(inner)) is patterns[0]

        assert reads == (2, patterns[::-1], patterns[1], patterns[:1])
        assert token == (version,)
        assert built_once

    def test_without_a_file_router_the_tree_routes_nothing(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("", "log")})
        with installed_apps(tmp_path, "audit"):
            tree, _app, _namespace = pages("audit")
            with routers_from(ENTRY_ROUTER):
                inner = list(tree[0].url_patterns)

        assert inner == []


class TestRoutedPages:
    """The page caches are sized by every build of one router version together."""

    def test_builds_add_up_until_the_version_moves(self) -> None:
        sized: list[int] = []
        pages_ = _RoutedPages()

        with patch(
            "next.urls.manager.fit_page_caches",
            lambda patterns: sized.append(len(list(patterns))),
        ):
            pages_.fit("next", ["a", "b"])
            pages_.fit("audit", ["c"])
            pages_.fit("next", ["a"])
            router_manager.reload()
            pages_.fit("audit", ["c"])

        assert sized == [2, 3, 2, 1]

    def test_an_explicit_tree_counts_beside_the_auto_assembly(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("", "log")})
        write_app(tmp_path, "blog", {"pages": ("about",)})
        urlconf = write_urlconf(
            tmp_path,
            "fit_urls",
            'path("audit/", pages("audit"))',
            'path("", include("next.urls"))',
        )

        with installed_apps(tmp_path, "audit", "blog", urlconf=urlconf):
            _status("/audit/log/", "/about/")
            builds = {
                getattr(build, "app", build): len(patterns)
                for build, patterns in routed_pages._builds.items()
            }

        assert builds == {"next": 1, "audit": 2}


class TestPlainSubdirectories:
    """A tree takes one plain directory name, so a claim on it compares exactly."""

    @pytest.mark.parametrize("subdir", ["", ".", "..", "./pages", "pages/log", "a\\b"])
    def test_pages_refuses_a_subdir_that_is_no_plain_name(
        self, tmp_path, subdir
    ) -> None:
        write_app(tmp_path, "audit", {"pages": ("log",)})

        with (
            installed_apps(tmp_path, "audit"),
            pytest.raises(ImproperlyConfigured, match="one plain directory name"),
        ):
            pages("audit", subdir)


class TestPagesFollowsPagesDir:
    """`pages()` mounts the tree router's `PAGES_DIR` tree unless told otherwise."""

    ROUTES = file_router_config_entry(app_dirs=True, pages_dir_name="routes")

    def test_the_default_tree_is_the_pages_dir_of_the_router(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"routes": ("log",), "staff": ("team",)})
        write_app(tmp_path, "blog", {"routes": ("about",)})
        urlconf = write_urlconf(
            tmp_path,
            "routes_urls",
            'path("audit/", pages("audit"))',
            'path("crew/", pages("audit", "staff", namespace="crew"))',
            'path("", include("next.urls"))',
        )

        with installed_apps(
            tmp_path,
            "audit",
            "blog",
            urlconf=urlconf,
            framework={"PAGE_BACKENDS": [self.ROUTES]},
        ):
            tree, _app, _namespace = pages("audit")
            routed = _routed()
            statuses = _status("/audit/log/", "/crew/team/", "/log/", "/about/")

        assert tree.mount.subdir == "routes"
        assert routed == [("about/", "page_about")]
        assert statuses == [200, 200, 404, 200]

    def test_a_missing_pages_dir_tree_is_improperly_configured(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("log",)})

        with (
            installed_apps(
                tmp_path, "audit", framework={"PAGE_BACKENDS": [self.ROUTES]}
            ),
            pytest.raises(
                ImproperlyConfigured, match=r"pages\('audit'\) names .*routes"
            ),
        ):
            pages("audit")

    def test_without_a_file_router_there_is_no_default(self, tmp_path) -> None:
        write_app(tmp_path, "audit")
        entry = {"BACKEND": "tests.support.routers.EntryRouter"}

        with (
            installed_apps(tmp_path, "audit", framework={"PAGE_BACKENDS": [entry]}),
            pytest.raises(ImproperlyConfigured, match="PAGE_BACKENDS lists none"),
        ):
            pages("audit")
