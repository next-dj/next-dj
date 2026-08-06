from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.checks import Error

from next.checks import reset_check_caches
from next.testing import override_next_settings
from next.urls import FileRouterBackend, PageRoot, RouterBackend
from next.urls.checks import (
    _collect_url_patterns,
    check_reverse_name_collisions,
    check_url_patterns,
)
from tests.support import importable_dir, patch_checks_router_manager_with_routers


@pytest.fixture(autouse=True)
def _reset_check_caches():
    reset_check_caches()
    yield
    reset_check_caches()


def _write_page(tree: Path, route: str) -> Path:
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text('template = "ok"\n')
    return page_file


def _write_virtual_page(tree: Path, route: str) -> Path:
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    template_file = directory / "template.djx"
    template_file.write_text("<h1>ok</h1>\n")
    return template_file


class _TreeRouter(RouterBackend):
    """Minimal router stub exposing the multi-tree traversal surface."""

    def __init__(
        self,
        app_trees: dict[str, Path] | None = None,
        root_trees: list[Path] | None = None,
        components_folder: str | None = None,
        skip_names: frozenset[str] | None = None,
    ) -> None:
        """Store app and root pages trees plus the names this backend refuses."""
        self._app_trees = dict(app_trees or {})
        self._root_trees = list(root_trees or [])
        self._components_folder = components_folder
        self._skip_names = skip_names or frozenset()

    def generate_urls(self) -> list:
        """Contribute no patterns, the checks read the reported trees instead."""
        return []

    def components_folder_name(self) -> str | None:
        """Name the folder the walk refuses to enter, or none at all."""
        return self._components_folder

    def skip_dir_names(self) -> frozenset[str]:
        """Name every other directory the walk refuses to enter."""
        return self._skip_names

    def page_roots(self) -> list[PageRoot]:
        roots = [
            PageRoot(path=tree, label=f"App '{name}'")
            for name, tree in self._app_trees.items()
        ]
        roots.extend(
            PageRoot(path=tree, label="Root" if index == 0 else f"Root ({tree})")
            for index, tree in enumerate(self._root_trees)
        )
        return roots


class _BrokenRouter(RouterBackend):
    """Router stub whose tree listing fails with OSError."""

    def generate_urls(self) -> list:
        """Contribute no patterns."""
        return []

    def page_roots(self) -> list[PageRoot]:
        msg = "pages roots unavailable"
        raise OSError(msg)


class TestDoublyMountedTree:
    """A tree mounted as an app and through `DIRS` really is routed twice."""

    def test_app_tree_also_in_dirs_reports_e015(self, tmp_path, settings) -> None:
        """The duplicate route is named, because the router does register it twice."""
        app = tmp_path / "shop"
        (app / "__init__.py").parent.mkdir(parents=True)
        (app / "__init__.py").write_text("")
        _write_page(app / "pages", "hello")
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[app / "pages"])

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop"]
            with patch_checks_router_manager_with_routers(routers=[router]):
                messages = check_url_patterns(None)
            routes = [str(pattern.pattern) for pattern in router.generate_urls()]

        assert routes == ["hello/", "hello/"]
        e015 = [m for m in messages if m.id == "next.E015"]
        assert len(e015) == 1
        assert "App 'shop'" in e015[0].msg
        assert "Root" in e015[0].msg


class TestCheckUrlPatterns:
    """`check_url_patterns` compares Django path strings across all trees."""

    def test_e015_for_equivalent_brackets_across_trees(self, tmp_path) -> None:
        """`[id]` and `[str:id]` from an app tree and a root tree conflict."""
        app_tree = tmp_path / "app_pages"
        root_tree = tmp_path / "root_pages"
        _write_page(app_tree, "things/[id]")
        _write_page(root_tree, "things/[str:id]")
        router = _TreeRouter(app_trees={"shop": app_tree}, root_trees=[root_tree])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        e015 = [m for m in messages if m.id == "next.E015"]
        assert len(e015) == 1
        assert '"things/<str:id>/"' in e015[0].msg
        assert "App 'shop'" in e015[0].msg
        assert "Root" in e015[0].msg

    def test_virtual_template_page_joins_e015_conflict(self, tmp_path) -> None:
        """A `template.djx`-only page collides with a real page from another tree."""
        app_tree = tmp_path / "app_pages"
        root_tree = tmp_path / "root_pages"
        _write_virtual_page(app_tree, "about")
        _write_page(root_tree, "about")
        router = _TreeRouter(app_trees={"shop": app_tree}, root_trees=[root_tree])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        e015 = [m for m in messages if m.id == "next.E015"]
        assert len(e015) == 1
        assert '"about/"' in e015[0].msg

    def test_identical_route_from_two_trees_is_e015_not_e039(self, tmp_path) -> None:
        """The same route trail in two trees is a path conflict, not a name one."""
        tree_a = tmp_path / "tree_a"
        tree_b = tmp_path / "tree_b"
        _write_page(tree_a, "blog")
        _write_page(tree_b, "blog")
        router = _TreeRouter(root_trees=[tree_a, tree_b])

        with patch_checks_router_manager_with_routers(routers=[router]):
            path_messages = check_url_patterns(None)
            name_messages = check_reverse_name_collisions(None)

        assert [m.id for m in path_messages] == ["next.E015"]
        assert name_messages == []

    def test_e028_for_normalised_duplicate_in_one_route(self, tmp_path) -> None:
        """`[a-b]/[a_b]` collapses to one parameter name and reports the page file."""
        page_file = _write_page(tmp_path, "[a-b]/[a_b]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        e028 = [m for m in messages if m.id == "next.E028"]
        assert len(e028) == 1
        assert "duplicate parameter" in e028[0].msg
        assert "'a_b'" in e028[0].msg
        assert e028[0].obj == str(page_file)

    def test_no_e028_for_distinct_parameter_names(self, tmp_path) -> None:
        """Unique parameter names in one route pass the check."""
        _write_page(tmp_path, "user/[id]/post/[slug]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        assert [m.id for m in messages] == []

    def test_e028_for_repeated_param_reports_page_file(self, tmp_path) -> None:
        """A plain repeated bracket name yields one E028 naming the page file."""
        page_file = _write_page(tmp_path, "user/[id]/[id]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        e028 = [m for m in messages if m.id == "next.E028"]
        assert len(e028) == 1
        assert "duplicate parameter" in e028[0].msg
        assert "'id'" in e028[0].msg
        assert e028[0].obj == str(page_file)

    def test_e028_message_lists_every_duplicate_name(self, tmp_path) -> None:
        """Two independent duplicates in one route both land in the message."""
        page_file = _write_page(tmp_path, "a/[id]/[int:id]/[slug]/[slug]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        e028 = [m for m in messages if m.id == "next.E028"]
        assert len(e028) == 1
        assert "['id', 'slug']" in e028[0].msg
        assert e028[0].obj == str(page_file)

    def test_e028_falls_back_to_parser_name_for_distinct_wildcards(
        self, tmp_path
    ) -> None:
        """Two wildcards with distinct names still fail and name the second one."""
        page_file = _write_page(tmp_path, "[[a]]/[[b]]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_url_patterns(None)

        e028 = [m for m in messages if m.id == "next.E028"]
        assert len(e028) == 1
        assert "['b']" in e028[0].msg
        assert e028[0].obj == str(page_file)

    def test_e028_reported_once_across_both_url_checks(self, tmp_path) -> None:
        """A duplicate route yields exactly one E028 over the whole check run."""
        _write_page(tmp_path, "user/[id]/[id]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = [*check_url_patterns(None), *check_reverse_name_collisions(None)]

        assert [m.id for m in messages] == ["next.E028"]

    def test_the_components_folder_a_backend_names_is_skipped(self, tmp_path) -> None:
        """The folder a backend names through the contract leaves the collection."""
        tree_a = tmp_path / "tree_a"
        tree_b = tmp_path / "tree_b"
        _write_page(tree_a, "_components/widget")
        _write_page(tree_b, "_components/widget")

        unskipped = _TreeRouter(root_trees=[tree_a, tree_b])
        with patch_checks_router_manager_with_routers(routers=[unskipped]):
            assert any(m.id == "next.E015" for m in check_url_patterns(None))

        reset_check_caches()
        skipped = _TreeRouter(
            root_trees=[tree_a, tree_b], components_folder="_components"
        )
        with patch_checks_router_manager_with_routers(routers=[skipped]):
            assert check_url_patterns(None) == []

    def test_a_skip_name_the_router_declares_leaves_the_collection(
        self, tmp_path
    ) -> None:
        """A directory the router itself refuses keeps its pages out of the routes."""
        tree_a = tmp_path / "tree_a"
        tree_b = tmp_path / "tree_b"
        _write_page(tree_a, "_drafts/wip")
        _write_page(tree_b, "_drafts/wip")
        router = _TreeRouter(root_trees=[tree_a, tree_b])

        with patch_checks_router_manager_with_routers(routers=[router]):
            assert any(m.id == "next.E015" for m in check_url_patterns(None))

        reset_check_caches()
        skipping = _TreeRouter(
            root_trees=[tree_a, tree_b], skip_names=frozenset({"_drafts"})
        )
        with patch_checks_router_manager_with_routers(routers=[skipping]):
            assert check_url_patterns(None) == []


class TestCheckReverseNameCollisions:
    """`check_reverse_name_collisions` flags routes sharing one reverse name."""

    def test_e039_lists_both_paths_once(self, tmp_path) -> None:
        """`foo-bar` and `foo_bar` collapse to one name and yield a single error."""
        _write_page(tmp_path, "foo-bar")
        _write_page(tmp_path, "foo_bar")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_reverse_name_collisions(None)

        assert len(messages) == 1
        assert messages[0].id == "next.E039"
        assert '"page_foo_bar"' in messages[0].msg
        assert "foo-bar/page.py" in messages[0].msg
        assert "foo_bar/page.py" in messages[0].msg

    def test_e039_ignores_routes_with_distinct_signatures(self, tmp_path) -> None:
        """`[year]/[month]` and literal `year/month` share a name, not a signature."""
        _write_page(tmp_path, "[year]/[month]")
        _write_page(tmp_path, "year/month")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_reverse_name_collisions(None)

        assert messages == []

    def test_e039_flags_routes_sharing_name_and_signature(self, tmp_path) -> None:
        """`a/[x]` and `a-[x]` share both the reverse name and the `x` parameter."""
        _write_page(tmp_path, "a/[x]")
        _write_page(tmp_path, "a-[x]")
        router = _TreeRouter(root_trees=[tmp_path])

        with patch_checks_router_manager_with_routers(routers=[router]):
            messages = check_reverse_name_collisions(None)

        assert len(messages) == 1
        assert messages[0].id == "next.E039"
        assert '"page_a_x"' in messages[0].msg

    def test_e039_name_uses_custom_url_name_template(self, tmp_path) -> None:
        """The reported name honours `URL_NAME_TEMPLATE` instead of `page_`."""
        _write_page(tmp_path, "foo-bar")
        _write_page(tmp_path, "foo_bar")
        router = _TreeRouter(root_trees=[tmp_path])

        with (
            override_next_settings(URL_NAME_TEMPLATE="next-{name}"),
            patch_checks_router_manager_with_routers(routers=[router]),
        ):
            messages = check_reverse_name_collisions(None)

        assert len(messages) == 1
        assert '"next-foo_bar"' in messages[0].msg
        assert "page_foo_bar" not in messages[0].msg

    def test_init_errors_returned_when_manager_missing(self) -> None:
        """A failed manager initialisation short-circuits into its own errors."""
        init_error = Error("router manager unavailable", id="next.E007")

        with patch(
            "next.urls.checks.get_router_manager", return_value=(None, [init_error])
        ):
            messages = check_reverse_name_collisions(None)

        assert messages == [init_error]

    def test_a_failing_tree_listing_leaves_the_url_checks_silent(self) -> None:
        """A router that cannot list its trees contributes none, and raises nothing.

        The finding itself is `next.E030` from the pages structure check, which
        is the one place that names it, so the run reports it exactly once.
        """
        with patch_checks_router_manager_with_routers(routers=[_BrokenRouter()]):
            path_messages = check_url_patterns(None)
            name_messages = check_reverse_name_collisions(None)

        assert path_messages == []
        assert name_messages == []


class TestCollectUrlPatterns:
    """`_collect_url_patterns` tolerates parser failures it cannot attribute."""

    def test_parser_value_error_skips_route_silently(self, tmp_path) -> None:
        """Plain ValueError from the parser drops the route without an error."""
        _write_page(tmp_path, "broken")
        errors: list[Error] = []

        with patch(
            "next.urls.checks.default_url_parser.parse_url_pattern",
            side_effect=ValueError("unparsable"),
        ):
            patterns = _collect_url_patterns(tmp_path, "Root", errors)

        assert patterns == []
        assert errors == []
