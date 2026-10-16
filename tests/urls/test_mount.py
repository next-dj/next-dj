from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client
from django.urls import URLResolver, reverse

from next.checks import reset_check_caches
from next.urls import page_reverse, pages, router_manager
from next.urls.backends import (
    MountDeclaration,
    _installed_app_directories,
    read_mount_declaration,
)
from next.urls.checks import check_app_mounts
from next.urls.manager import _RoutedPages, routed_pages
from next.urls.mount import MountedTree
from next.utils import PageRoot
from tests.support import (
    NAMESPACED_URLCONF,
    check_ids,
    file_router,
    file_router_config_entry,
    installed_apps,
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


class TestMountedAppTrees:
    """An app declares the prefix of its page trees on its own config class."""

    def test_mount_routes_the_pages_tree_under_its_prefix(self, tmp_path) -> None:
        write_app(tmp_path, "shop", {"pages": ("", "cart")}, mount="shop/")

        with installed_apps(tmp_path, "shop"):
            routed = _routed()
            statuses = _status("/shop/cart/", "/cart/")

        assert routed == [("shop/", "page_shop"), ("shop/cart/", "page_shop_cart")]
        assert statuses == [200, 404]

    def test_mounts_routes_each_subdirectory_under_its_prefix(self, tmp_path) -> None:
        write_app(
            tmp_path,
            "wallets",
            {"pages": ("", "ops"), "staff_pages": ("team",)},
            mounts={"pages": "w/<int:wallet>/", "staff_pages": "staff/"},
        )

        with installed_apps(tmp_path, "wallets"):
            routed = _routed()
            roots = _roots()
            statuses = _status("/w/3/ops/", "/staff/team/", "/team/")

        assert routed == [
            ("w/<int:wallet>/", "page_w_int_wallet"),
            ("w/<int:wallet>/ops/", "page_w_int_wallet_ops"),
            ("staff/team/", "page_staff_team"),
        ]
        assert [
            (root.label, root.prefix, root.subdir, root.source) for root in roots
        ] == [
            ("App 'wallets'", "w/<int:wallet>/", "pages", "mounts"),
            ("App 'wallets' (staff_pages)", "staff/", "staff_pages", "mounts"),
        ]
        assert statuses == [200, 200, 404]

    @pytest.mark.parametrize(
        ("declared", "prefix", "subdir"),
        [
            ({"mount": "/shop"}, "shop/", "pages"),
            ({"mount": "shop///"}, "shop/", "pages"),
        ],
        ids=["leading_slash", "trailing_slashes"],
    )
    def test_a_declaration_is_normalised(self, tmp_path, declared, prefix, subdir):
        write_app(tmp_path, "shop", {"pages": ("cart",)}, **declared)

        with installed_apps(tmp_path, "shop"):
            routed = _routed()
            (root,) = _roots()

        assert routed == [("shop/cart/", "page_shop_cart")]
        assert (root.prefix, root.subdir) == (prefix, subdir)

    def test_a_mounts_key_naming_no_directory_routes_nothing(self, tmp_path) -> None:
        write_app(
            tmp_path, "shop", {"pages": ("cart",)}, mounts={"pages": "", "gone": "x/"}
        )

        with installed_apps(tmp_path, "shop"):
            routed = _routed()
            roots = _roots()

        assert routed == [("cart/", "page_cart")]
        assert [root.subdir for root in roots] == ["pages"]

    def test_prefixed_trees_lead_then_plain_app_trees_then_root_trees(
        self, tmp_path
    ) -> None:
        write_app(tmp_path, "blog", {"pages": ("about",)})
        write_app(tmp_path, "shop", {"pages": ("cart",)}, mount="shop/")
        write_app(tmp_path, "docs", {"pages": ("guide",)}, mounts={"pages": ""})
        write_page(tmp_path / "site", "contact")

        with installed_apps(tmp_path, "blog", "shop", "docs", dirs=[tmp_path / "site"]):
            routed = _routed()
            roots = _roots()

        assert [route for route, _name in routed] == [
            "shop/cart/",
            "about/",
            "guide/",
            "contact/",
        ]
        assert [(root.app, root.source) for root in roots] == [
            ("shop", "mounts"),
            ("blog", "root"),
            ("docs", "mounts"),
            ("", "root"),
        ]

    def test_a_dotted_app_is_reported_by_its_label(self, tmp_path) -> None:
        write_app(tmp_path, "corp.billing", {"pages": ("pay",)}, mount="bill/")

        with installed_apps(tmp_path, "corp.billing"):
            (root,) = _roots()

        assert (root.app, root.label) == ("billing", "App 'corp.billing'")

    def test_a_subdirectory_resolves_against_the_app_path_of_the_snapshot(
        self, tmp_path
    ) -> None:
        (tmp_path / "old" / "staff_pages").mkdir(parents=True)
        (tmp_path / "new" / "staff_pages").mkdir(parents=True)
        router = file_router(app_dirs=True)

        paths = [
            router._get_app_tree_path("shop", directories, "staff_pages")
            for directories in (
                {"shop": tmp_path / "old"},
                {"shop": tmp_path / "old"},
                {"shop": tmp_path / "new"},
                {},
            )
        ]

        assert paths == [
            tmp_path / "old" / "staff_pages",
            tmp_path / "old" / "staff_pages",
            tmp_path / "new" / "staff_pages",
            None,
        ]

    def test_a_snapshot_without_labels_names_the_app_by_its_last_part(
        self, tmp_path
    ) -> None:
        write_page(tmp_path / "pages", "pay")
        router = file_router(app_dirs=True)

        with patch(
            "next.urls.backends._installed_app_directories",
            return_value={"corp.billing": tmp_path},
        ):
            (root,) = router.page_roots()
            routed = [str(pattern.pattern) for pattern in router.generate_urls()]

        assert (root.app, root.source, root.prefix) == ("billing", "root", "")
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
        assert [(root.prefix, root.source, root.namespace) for root in roots] == [
            ("", "root", "next"),
            ("", "root", "next"),
            ("", "root", "next"),
        ]

    def test_an_empty_mount_routes_like_no_declaration(self, tmp_path) -> None:
        names = self._project(tmp_path)
        with installed_apps(tmp_path, *names, dirs=[tmp_path / "site"]):
            undeclared = _routed()
        write_app(tmp_path / "declared", "blog", {"pages": ("", "about")}, mount="")
        write_page(tmp_path / "declared" / "blog" / "pages", "posts/[slug]")

        with installed_apps(tmp_path / "declared", "blog"):
            declared = _routed()

        assert declared == undeclared[:3]

    def test_an_unprefixed_view_carries_no_mount(self, tmp_path) -> None:
        names = self._project(tmp_path)

        with installed_apps(tmp_path, *names):
            views = [pattern.callback for pattern in router_manager]

        assert not any(hasattr(view, "next_route_prefix") for view in views)


class TestInvalidDeclarations:
    """A declaration the router refuses routes the app as if it declared nothing."""

    @pytest.mark.parametrize(
        "declared",
        [
            {"mount": 3},
            {"mounts": ["shop/"]},
            {"mounts": {"pages": 3}},
            {"mounts": {3: "shop/"}},
            {"mount": "shop/", "mounts": {"pages": "shop/"}},
            {"mount": "[slug]/"},
            {"mounts": {"pages": "w/[int:wallet]/"}},
        ],
        ids=[
            "mount_not_str",
            "mounts_not_dict",
            "mounts_value_not_str",
            "mounts_key_not_str",
            "both",
            "bracketed_mount",
            "bracketed_mounts",
        ],
    )
    def test_the_app_routes_at_the_root(self, tmp_path, declared) -> None:
        write_app(tmp_path, "shop", {"pages": ("", "cart")}, **declared)

        with installed_apps(tmp_path, "shop"):
            routed = _routed()
            (root,) = _roots()

        assert routed == [("", "page_"), ("cart/", "page_cart")]
        assert (root.prefix, root.source) == ("", "root")

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            ({}, MountDeclaration()),
            ({"mount": "shop"}, MountDeclaration(trees=((None, "shop/"),))),
            (
                {"mounts": {"pages": "", "staff_pages": "/staff"}},
                MountDeclaration(trees=(("pages", ""), ("staff_pages", "staff/"))),
            ),
            (
                {"mount": 3},
                MountDeclaration(refusal="declares mount as int instead of a str"),
            ),
            (
                {"mounts": ("pages",)},
                MountDeclaration(
                    refusal="declares mounts as something other than a dict of "
                    "str to str"
                ),
            ),
            (
                {"mount": "a/", "mounts": {}},
                MountDeclaration(refusal="declares both mount and mounts"),
            ),
            (
                {"mounts": {"pages": "[a]/", "more": "b/", "odd": "c]/"}},
                MountDeclaration(bracketed=("[a]/", "c]/")),
            ),
            (
                {"mounts": {"pages": "", "/pages/": "a/", "a/b": "", "..": ""}},
                MountDeclaration(
                    refusal="declares mounts keys ['/pages/', 'a/b', '..'], which "
                    "are no plain directory names"
                ),
            ),
        ],
        ids=[
            "undeclared",
            "mount",
            "mounts",
            "mount_type",
            "mounts_type",
            "both",
            "brackets",
            "unplain_keys",
        ],
    )
    def test_what_a_declaration_reads_as(self, declared, expected) -> None:
        assert read_mount_declaration(SimpleNamespace(**declared)) == expected


class TestDeclarationSnapshot:
    """The declarations are read once per app registry snapshot, not per call."""

    def test_one_snapshot_serves_every_read(self, tmp_path) -> None:
        write_app(tmp_path, "shop", mount="shop/")

        with (
            installed_apps(tmp_path, "shop"),
            patch(
                "next.urls.backends.read_mount_declaration",
                wraps=read_mount_declaration,
            ) as reads,
        ):
            first = _installed_app_directories()
            second = _installed_app_directories()
            _roots()
            _routed()

        assert first is second
        assert reads.call_count == 1

    def test_installing_an_app_takes_a_new_snapshot(self, tmp_path) -> None:
        write_app(tmp_path, "shop", mount="shop/")
        before = _installed_app_directories()

        with installed_apps(tmp_path, "shop"):
            during = _installed_app_directories()

        assert during is not before
        assert "shop" in during
        assert "shop" not in before


class TestPrefixKwargsReachThePage:
    """A kwarg the prefix captures reaches `DUrl` on a page deep inside the tree."""

    def test_a_mounts_prefix(self, tmp_path) -> None:
        write_app(
            tmp_path, "wallets", {"pages": ()}, mounts={"pages": "w/<int:wallet>/"}
        )
        write_page(
            tmp_path / "wallets" / "pages", "ops/[int:pk]", _ids_page("wallet", "pk")
        )

        with installed_apps(tmp_path, "wallets"):
            response = Client().get("/w/3/ops/4/")

        assert response.status_code == 200
        assert response.content.decode().strip() == "3-4"

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


class TestReverse:
    """A prefixed page reverses by its full trail, a `pages()` one in its namespace."""

    def test_a_mounted_page_by_trail_and_by_name(self, tmp_path) -> None:
        write_app(
            tmp_path,
            "wallets",
            {"pages": ("ops/[int:pk]",)},
            mounts={"pages": "w/<int:wallet>/"},
        )

        with installed_apps(tmp_path, "wallets", urlconf=NAMESPACED_URLCONF):
            by_trail = page_reverse("w/[int:wallet]/ops/[int:pk]", wallet=3, pk=4)
            by_name = reverse(
                "next:page_w_int_wallet_ops_int_pk", kwargs={"wallet": 3, "pk": 4}
            )

        assert by_trail == by_name == "/w/3/ops/4/"

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
    """`pages()` mounts a tree where a URLconf includes it and outranks `mounts`."""

    def test_the_urlconf_wins_over_the_declared_prefix(self, tmp_path) -> None:
        write_app(
            tmp_path,
            "wallets",
            {"pages": ("ops",), "staff_pages": ("team",)},
            mounts={"pages": "w/", "staff_pages": "staff/"},
        )
        urlconf = write_urlconf(
            tmp_path,
            "priority_urls",
            'path("", include("next.urls"))',
            'path("crew/", pages("wallets", "staff_pages", namespace="crew"))',
        )

        with installed_apps(tmp_path, "wallets", urlconf=urlconf):
            routed = _routed()
            roots = _roots()
            statuses = _status("/crew/team/", "/staff/team/", "/w/ops/")

        assert routed == [("w/ops/", "page_w_ops")]
        assert [(root.subdir, root.prefix, root.source) for root in roots] == [
            ("pages", "w/", "mounts"),
            ("staff_pages", "crew/", "urls"),
        ]
        assert roots[1].namespace == "crew"
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
        entry = {"BACKEND": "tests.support.routers.EntryRouter"}

        with installed_apps(tmp_path, "audit"):
            tree, _app, _namespace = pages("audit")
            with patch.object(
                router_manager, "_get_next_pages_config", return_value=[entry]
            ):
                router_manager.reload()
                inner = list(tree[0].url_patterns)
            router_manager.reload()

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

    def test_an_unplain_mounts_key_routes_the_app_at_the_root(self, tmp_path) -> None:
        write_app(tmp_path, "shop", {"pages": ("cart",)}, mounts={"./pages": "x/"})

        with installed_apps(tmp_path, "shop"):
            routed = _routed()
            (root,) = _roots()

        assert routed == [("cart/", "page_cart")]
        assert (root.prefix, root.source) == ("", "root")


class TestMountFollowsPagesDir:
    """`mount` moves the tree the router would mount by default, its `PAGES_DIR`."""

    def test_mount_moves_the_pages_dir_tree_of_the_router(self, tmp_path) -> None:
        write_app(tmp_path, "shop", {"routes": ("cart",)}, mount="shop/")
        entry = file_router_config_entry(app_dirs=True, pages_dir_name="routes")

        with installed_apps(tmp_path, "shop", framework={"PAGE_BACKENDS": [entry]}):
            routed = _routed()
            (root,) = _roots()

        assert routed == [("shop/cart/", "page_shop_cart")]
        assert (root.subdir, root.prefix, root.source) == ("routes", "shop/", "mounts")


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

    def test_the_default_claims_a_declared_pages_dir_tree(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"routes": ("log",)}, mount="a/")
        urlconf = write_urlconf(tmp_path, "claim_urls", 'path("b/", pages("audit"))')

        with installed_apps(
            tmp_path,
            "audit",
            urlconf=urlconf,
            framework={"PAGE_BACKENDS": [self.ROUTES]},
        ):
            reset_check_caches()
            messages = check_app_mounts(None)
            statuses = _status("/b/log/", "/a/log/")

        assert check_ids(messages) == ["next.W127"]
        assert "'routes'" in messages[0].msg
        assert statuses == [200, 404]

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
