from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from django.core.checks import Error
from django.test import override_settings

from next.conf.signals import settings_reloaded
from next.discovery import (
    PageRootsError,
    discover_page_registrations,
    get_page_roots,
    get_pages_directories,
    get_router_manager,
    iter_page_tree_component_folders,
    iter_scanned_page_pairs,
    page_tree_skip_names,
    read_page_roots,
    reset_router_manager_cache,
)
from next.pages import page
from next.pages.ports import PageScanImpl
from next.ports import PortSlot
from next.urls import FileRouterBackend, PageRoot, RouterBackend, RouterFactory
from next.urls.dispatcher import scan_pages_tree
from next.urls.ports import RouterAccessImpl
from next.utils import walk_page_tree
from tests.support import (
    MalformedRootsRouter,
    OddSkipNamesRouter,
    RaisingComponentsRouter,
    RaisingRootsRouter,
    RaisingSkipNamesRouter,
    SkippingRouter,
    file_router,
    file_router_config_entry,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


def _labelled_root(index: int, tree: Path) -> PageRoot:
    return PageRoot(path=tree, label="Root" if index == 0 else f"Root ({tree})")


def _write_page(tree: Path, route: str, source: str = 'template = "ok"\n') -> Path:
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(source)
    return page_file


class _RootTreeRouter(RouterBackend):
    """Third-party backend that reports root page trees and nothing else."""

    def __init__(self, root_trees: list[Path]) -> None:
        self._root_trees = list(root_trees)

    def generate_urls(self) -> list:
        return []

    def page_roots(self) -> list[PageRoot]:
        return [
            _labelled_root(index, tree) for index, tree in enumerate(self._root_trees)
        ]


class _CustomFolderRouter(FileRouterBackend):
    """File router subclass that registers components from another folder."""

    @staticmethod
    def _resolve_components_folder_name() -> str:
        return "_widgets"


class _WidgetsFolderRouter(FileRouterBackend):
    """File router subclass registering components from a `widgets` folder."""

    @staticmethod
    def _resolve_components_folder_name() -> str:
        return "widgets"


@dataclass
class _UnhashableRouter(RouterBackend):
    """Third-party router written as a plain dataclass, so `__hash__` is `None`."""

    tree: Path

    def generate_urls(self) -> list:
        return []

    def page_roots(self) -> list[PageRoot]:
        return [PageRoot(path=self.tree, label="Root")]


class _TwoLabelRouter(RouterBackend):
    """Router reporting one tree twice, as an app tree and as a configured root."""

    def __init__(self, tree: Path) -> None:
        self._tree = tree

    def generate_urls(self) -> list:
        return []

    def page_roots(self) -> list[PageRoot]:
        return [
            PageRoot(path=self._tree, label="App 'shop'"),
            PageRoot(path=self._tree, label="Root"),
        ]


@contextmanager
def _walk_spy() -> Iterator[MagicMock]:
    """Count the tree walks a check seam runs, keeping the real walk."""
    with patch("next.discovery.walk_page_tree", wraps=walk_page_tree) as spy:
        yield spy


def _walked_trees(spy: MagicMock) -> list[Path]:
    """Return the root of every walk the spy recorded, in order."""
    return [call.args[0] for call in spy.call_args_list]


class _CountingRouterAccess(RouterAccessImpl):
    """Router port that counts the managers a check run asks it to build."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.built = 0
        self._failure = failure

    def create_manager(self):
        self.built += 1
        if self._failure is not None:
            raise self._failure
        return super().create_manager()


@contextmanager
def _counting_router_access(
    failure: Exception | None = None,
) -> Iterator[_CountingRouterAccess]:
    """Bind a counting router port into the slot the discovery pass reads."""
    access = _CountingRouterAccess(failure)
    slot: PortSlot = PortSlot("router access port")
    slot.set(access)
    with patch("next.discovery.router_access_slot", slot):
        yield access


class TestRouterManagerCache:
    """`get_router_manager` reuses one manager per check run."""

    def test_built_once_across_repeated_calls(self) -> None:
        with _counting_router_access() as access:
            first = get_router_manager()
            second = get_router_manager()
            third = get_router_manager()
        assert first is second is third
        assert access.built == 1

    def test_explicit_reset_forces_rebuild(self) -> None:
        with _counting_router_access() as access:
            first, _errors = get_router_manager()
            reset_router_manager_cache()
            second, _again = get_router_manager()
        assert access.built == 2
        assert first is not second

    def test_settings_reloaded_signal_resets_cache(self) -> None:
        with _counting_router_access() as access:
            get_router_manager()
            settings_reloaded.send(sender=None)
            get_router_manager()
        assert access.built == 2

    def test_the_cached_manager_reports_the_routers_of_the_settings(
        self, tmp_path: Path
    ) -> None:
        """A check run reads its routes off this manager, so it carries the trees."""
        _write_page(tmp_path, "blog")
        entry = file_router_config_entry(pages_dir=tmp_path)

        with (
            override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}),
            _counting_router_access(),
        ):
            manager, errors = get_router_manager()

        assert errors == []
        assert [
            root.path for backend in manager.backends for root in backend.page_roots()
        ] == [tmp_path.resolve()]

    def test_an_unbound_port_is_reported_rather_than_raised(self) -> None:
        # `manage.py check` on a project whose app never started reports the
        # misconfiguration, where a traceback would report nothing at all.
        with patch("next.discovery.router_access_slot", PortSlot("router access port")):
            manager, errors = get_router_manager()

        assert manager is None
        assert [error.id for error in errors] == ["next.E007"]
        assert "unbound" in errors[0].msg

    def test_init_error_result_is_cached(self) -> None:
        with _counting_router_access(ImportError("boom")) as access:
            manager, errors = get_router_manager()
            second_manager, second_errors = get_router_manager()
        assert manager is None
        assert second_manager is None
        assert errors is second_errors
        assert errors[0].id == "next.E007"
        assert access.built == 1


@contextmanager
def _manager_over(routers: list[object]) -> Iterator[None]:
    """Point the discovery pass at exactly these routers."""
    manager = MagicMock()
    manager.backends = tuple(routers)
    with patch("next.discovery.get_router_manager", return_value=(manager, [])):
        yield


class _CountingPageScan(PageScanImpl):
    """Page-scan port that records the managers a discovery pass hands it."""

    def __init__(self) -> None:
        self.managers: list[object] = []

    def load_scanned_page_modules(self, router_manager):
        self.managers.append(router_manager)
        return super().load_scanned_page_modules(router_manager)


@contextmanager
def _counting_page_scan() -> Iterator[_CountingPageScan]:
    """Bind a counting page-scan port into the slot the discovery pass reads."""
    scan = _CountingPageScan()
    slot: PortSlot = PortSlot("page scan port")
    slot.set(scan)
    with patch("next.discovery.page_scan_slot", slot):
        yield scan


class TestPageRegistrationDiscovery:
    """`discover_page_registrations` imports the routed pages of a check run."""

    def test_a_routed_page_module_is_executed(self, tmp_path: Path) -> None:
        """Execution is what puts the decorators of a page.py into the registry."""
        page_file = _write_page(
            tmp_path,
            "blog",
            "from next.pages import context\n\n\n"
            '@context("greeting")\n'
            "def greeting() -> str:\n"
            '    return "hi"\n',
        )
        with _manager_over([_RootTreeRouter([tmp_path])]):
            loaded = discover_page_registrations()
        assert loaded == [("blog", page_file)]
        bindings = page.zone_bindings()[page_file.resolve()]
        assert [binding.key for binding in bindings] == ["greeting"]

    def test_a_page_that_cannot_be_imported_is_left_out(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog").write_text("raise RuntimeError('boom')\n")
        with _manager_over([_RootTreeRouter([tmp_path])]):
            loaded = discover_page_registrations()
        assert loaded == []

    def test_a_second_call_runs_the_pass_again(self, tmp_path: Path) -> None:
        """A repeat is what lets a check run see the tree as it stands.

        Django orders its checks freely, so no memo may sit in front of the scan.
        """
        page_file = _write_page(tmp_path, "blog")
        with (
            _manager_over([_RootTreeRouter([tmp_path])]),
            _counting_page_scan() as scan,
        ):
            first = discover_page_registrations()
            second = discover_page_registrations()

        assert first == second == [("blog", page_file)]
        assert len(scan.managers) == 2

    def test_a_tree_rescanned_after_a_reset_reports_the_new_page(
        self, tmp_path: Path
    ) -> None:
        """The per-run walk is what freezes the tree, and a reset is what thaws it."""
        blog = _write_page(tmp_path, "blog")
        router = _RootTreeRouter([tmp_path])
        with _manager_over([router]):
            first = discover_page_registrations()
            about = _write_page(tmp_path, "about")
            frozen = discover_page_registrations()
            reset_router_manager_cache()
            after = discover_page_registrations()

        assert first == frozen == [("blog", blog)]
        assert sorted(after) == sorted([("about", about), ("blog", blog)])

    def test_a_given_manager_is_the_one_walked(self, tmp_path: Path) -> None:
        """A caller that resolved its own routers is not sent back to the slot."""
        page_file = _write_page(tmp_path, "blog")
        manager = MagicMock()
        manager.backends = (_RootTreeRouter([tmp_path]),)
        with (
            patch("next.discovery.get_router_manager") as resolve,
            _counting_page_scan() as scan,
        ):
            loaded = discover_page_registrations(manager)
        assert loaded == [("blog", page_file)]
        assert resolve.call_count == 0
        assert scan.managers == [manager]

    def test_an_uninitialised_manager_imports_nothing(self) -> None:
        with (
            patch(
                "next.discovery.get_router_manager",
                return_value=(None, [Error("boom", id="next.E007")]),
            ),
            _counting_page_scan() as scan,
        ):
            loaded = discover_page_registrations()
        assert loaded == []
        assert scan.managers == []


class TestScannedPairsCache:
    """`iter_scanned_page_pairs` materialises one scan per router per run."""

    def test_two_consumptions_scan_once(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _RootTreeRouter([tmp_path])

        with _walk_spy() as spy:
            first = list(iter_scanned_page_pairs(router))
            second = list(iter_scanned_page_pairs(router))

        assert first == second
        assert spy.call_count == 1

    def test_cached_pairs_match_direct_scan(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        _write_page(tmp_path, "docs/guide")
        router = _RootTreeRouter([tmp_path])

        cached = list(iter_scanned_page_pairs(router))
        direct = list(scan_pages_tree(tmp_path))

        assert cached == direct

    def test_distinct_routers_cache_independently(self, tmp_path: Path) -> None:
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        _write_page(tree_a, "one")
        _write_page(tree_b, "two")
        router_a = _RootTreeRouter([tree_a])
        router_b = _RootTreeRouter([tree_b])

        with _walk_spy() as spy:
            pairs_a = list(iter_scanned_page_pairs(router_a))
            list(iter_scanned_page_pairs(router_a))
            pairs_b = list(iter_scanned_page_pairs(router_b))
            list(iter_scanned_page_pairs(router_b))

        assert _walked_trees(spy) == [tree_a, tree_b]
        assert pairs_a != pairs_b

    def test_explicit_reset_rescans(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _RootTreeRouter([tmp_path])

        with _walk_spy() as spy:
            list(iter_scanned_page_pairs(router))
            reset_router_manager_cache()
            list(iter_scanned_page_pairs(router))

        assert spy.call_count == 2

    def test_settings_reloaded_signal_rescans(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _RootTreeRouter([tmp_path])

        with _walk_spy() as spy:
            list(iter_scanned_page_pairs(router))
            settings_reloaded.send(sender=None)
            list(iter_scanned_page_pairs(router))

        assert spy.call_count == 2

    def test_new_pages_visible_only_after_reset(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _RootTreeRouter([tmp_path])

        before = list(iter_scanned_page_pairs(router))
        _write_page(tmp_path, "about")
        frozen = list(iter_scanned_page_pairs(router))
        reset_router_manager_cache()
        after = list(iter_scanned_page_pairs(router))

        assert frozen == before
        assert len(after) == len(before) + 1


class TestEveryPagesRootIsScanned:
    """`iter_scanned_page_pairs` walks every configured pages root."""

    def test_pairs_come_from_all_roots(self, tmp_path: Path) -> None:
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        page_a = _write_page(tree_a, "blog")
        page_b = _write_page(tree_b, "docs")
        router = _RootTreeRouter([tree_a, tree_b])

        with _walk_spy() as spy:
            pairs = list(iter_scanned_page_pairs(router))

        assert _walked_trees(spy) == [tree_a, tree_b]
        assert [page_file for _url, page_file in pairs] == [page_a, page_b]

    def test_directories_are_reported_without_duplicates(self, tmp_path: Path) -> None:
        # A repeat sits between two distinct roots, so a first-root-only walk
        # and a walk that keeps duplicates both fail this.
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        _write_page(tree_a, "blog")
        _write_page(tree_b, "docs")
        router = _RootTreeRouter([tree_a, tree_b, tree_a])

        assert get_pages_directories(router) == [tree_a, tree_b]

    def test_symlinked_spelling_of_one_tree_collapses(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        _write_page(real, "blog")
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        router = _RootTreeRouter([real, linked])

        directories = get_pages_directories(router)

        assert directories == [real]
        assert list(iter_scanned_page_pairs(router)) == list(scan_pages_tree(real))

    def test_one_tree_under_two_labels_is_scanned_once(self, tmp_path: Path) -> None:
        # An app tree also listed in DIRS is routed twice for real, so the roots
        # keep both entries while the scan behind the page checks walks it once.
        _write_page(tmp_path, "blog")
        router = _TwoLabelRouter(tmp_path)

        assert [root.label for root in get_page_roots(router)] == ["App 'shop'", "Root"]
        assert get_pages_directories(router) == [tmp_path]

    def test_reported_spelling_survives_the_collapse(self, tmp_path: Path) -> None:
        # The page registries key on the path the module was loaded by, so the
        # router's own spelling has to come back out, not the resolved one.
        real = tmp_path / "real"
        _write_page(real, "blog")
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        router = _RootTreeRouter([linked, real])

        assert get_pages_directories(router) == [linked]


class TestPageRootsAreTheRoutersOwn:
    """`get_page_roots` reports the trees a router routes and invents none."""

    def test_a_router_that_routes_nothing_reports_no_tree(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A `pages` beside the process is no page root. `next.W002` names it.
        _write_page(tmp_path / "pages", "hello")
        monkeypatch.chdir(tmp_path)

        assert get_page_roots(_RootTreeRouter(root_trees=[])) == []

    def test_the_reported_tree_is_the_only_tree(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _write_page(tmp_path / "pages", "hello")
        configured = tmp_path / "shell"
        configured.mkdir()
        monkeypatch.chdir(tmp_path)
        router = _RootTreeRouter([configured])

        assert get_page_roots(router) == [PageRoot(path=configured, label="Root")]


class TestPageTreeSkipNames:
    """The skip set is the router's own, both halves read off its contract."""

    def test_a_backend_refusing_nothing_and_naming_no_folder_skips_nothing(
        self,
    ) -> None:
        assert page_tree_skip_names(_RootTreeRouter([])) == frozenset()

    def test_the_names_the_backend_refuses_are_the_skip_set(self) -> None:
        router = SkippingRouter([], frozenset({"api", "_drafts"}))

        assert page_tree_skip_names(router) == frozenset({"api", "_drafts"})

    def test_the_components_folder_of_the_backend_joins_the_skip_set(self) -> None:
        router = SkippingRouter([], frozenset({"api"}))

        with patch.object(SkippingRouter, "components_folder_name", return_value="wid"):
            assert page_tree_skip_names(router) == frozenset({"api", "wid"})

    def test_the_dirs_of_another_backend_entry_name_no_skip_name_here(
        self, tmp_path: Path
    ) -> None:
        # A `blog` that one entry refuses is a route of the next entry's tree,
        # so the skip set of a router may never gather what another declared.
        tree = tmp_path / "site"
        _write_page(tree, "blog")
        entries = [
            file_router_config_entry(dirs=["blog"]),
            file_router_config_entry(pages_dir=tree),
        ]

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": entries}):
            router = RouterFactory.create_backend(entries[1])
            routes = [url for url, _page in iter_scanned_page_pairs(router)]

            assert "blog" not in page_tree_skip_names(router)

        assert routes == ["blog"]

    def test_a_raising_components_folder_name_costs_only_that_name(self) -> None:
        router = SkippingRouter([], frozenset({"api"}))

        with patch.object(
            SkippingRouter,
            "components_folder_name",
            side_effect=RuntimeError("components folder unavailable"),
        ):
            assert page_tree_skip_names(router) == frozenset({"api"})

    def test_a_malformed_components_folder_name_costs_only_that_name(self) -> None:
        router = SkippingRouter([], frozenset({"api"}))

        with patch.object(
            SkippingRouter, "components_folder_name", return_value=Path("widgets")
        ):
            assert page_tree_skip_names(router) == frozenset({"api"})

    def test_a_raising_skip_set_refuses_no_directory(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = RaisingSkipNamesRouter([tmp_path])

        assert page_tree_skip_names(router) == frozenset()
        assert [url for url, _page in iter_scanned_page_pairs(router)] == ["blog"]

    def test_a_skip_set_answered_as_a_string_costs_no_skip_name(self) -> None:
        # Iterating the string would refuse the directories `a`, `p` and `i`.
        assert page_tree_skip_names(OddSkipNamesRouter([])) == frozenset()

    def test_a_skip_set_holding_more_than_names_keeps_the_names(self) -> None:
        router = SkippingRouter([], frozenset({"api"}))

        with patch.object(
            SkippingRouter, "skip_dir_names", return_value=["api", 7, None]
        ):
            assert page_tree_skip_names(router) == frozenset({"api"})

    def test_a_raising_router_is_asked_for_its_contract_once_per_run(
        self, tmp_path: Path
    ) -> None:
        # Three checks ask the same questions, and a failing router would
        # otherwise write one traceback per asking check.
        router = RaisingComponentsRouter([tmp_path])
        with patch.object(
            RaisingComponentsRouter,
            "components_folder_name",
            side_effect=RuntimeError("components folder unavailable"),
        ) as asked:
            page_tree_skip_names(router)
            page_tree_skip_names(router)
            list(iter_page_tree_component_folders(router))

        assert asked.call_count == 1


class TestPageTreeComponentFolders:
    """The folders a check discovers are the ones the router walk registers."""

    def _write_component(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "component.djx").write_text("<p>c</p>\n")
        return folder

    def test_folders_carry_their_tree_root_and_route_trail(
        self, tmp_path: Path
    ) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        top = self._write_component(tree / "_components")
        nested = self._write_component(tree / "blog" / "_components")
        router = file_router(app_dirs=False, dirs=[tree])

        found = sorted(iter_page_tree_component_folders(router))

        assert found == sorted([(top, tree, ""), (nested, tree, "blog")])

    def test_a_folder_under_a_skipped_directory_is_not_reached(
        self, tmp_path: Path
    ) -> None:
        # The router never registers what `_drafts` holds, so neither may the check.
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        self._write_component(tree / "_drafts" / "_components")
        entry = file_router_config_entry(pages_dir=tree, dirs=["_drafts"])

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            router = RouterFactory.create_backend(entry)
            assert list(iter_page_tree_component_folders(router)) == []

    def test_a_backend_naming_no_components_folder_reports_none(
        self, tmp_path: Path
    ) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        self._write_component(tree / "_components")

        assert list(iter_page_tree_component_folders(_RootTreeRouter([tree]))) == []

    def test_the_folder_name_the_backend_names_is_the_one_found(
        self, tmp_path: Path
    ) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        widgets = self._write_component(tree / "widgets")
        router = _WidgetsFolderRouter(file_router_config_entry(pages_dir=tree))

        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    file_router_config_entry(pages_dir=tree, dirs=["widgets"])
                ]
            }
        ):
            assert list(iter_page_tree_component_folders(router)) == [
                (widgets, tree, "")
            ]

    def test_pages_and_folders_come_from_one_walk(self, tmp_path: Path) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        self._write_component(tree / "_components")
        router = file_router(app_dirs=False, dirs=[tree])

        with _walk_spy() as spy:
            pairs = list(iter_scanned_page_pairs(router))
            folders = list(iter_page_tree_component_folders(router))

        assert spy.call_count == 1
        assert len(pairs) == 1
        assert len(folders) == 1

    def test_a_raising_components_folder_name_reports_no_folder(
        self, tmp_path: Path
    ) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        self._write_component(tree / "_components")

        assert (
            list(iter_page_tree_component_folders(RaisingComponentsRouter([tree])))
            == []
        )


class TestFileRouterWalkParity:
    """The check walk finds exactly the pages the file router's own walk finds."""

    def _build_tree(self, root: Path) -> None:
        _write_page(root, "blog")
        _write_page(root, "blog/[slug]")
        _write_page(root, "_components/card")
        _write_page(root, "_drafts/wip")
        _write_page(root, "deep/nested/leaf")
        (root / "virtual").mkdir(parents=True, exist_ok=True)
        (root / "virtual" / "template.djx").write_text("<p>ok</p>\n")

    @pytest.mark.parametrize(
        "dirs",
        [[], ["_drafts"], ["_drafts", "deep"], ["does_not_exist/nested"]],
        ids=["no-dirs", "one-skip-name", "two-skip-names", "path-shaped-skip-name"],
    )
    def test_the_two_walks_agree_pair_for_pair(self, tmp_path: Path, dirs) -> None:
        tree = tmp_path / "shell"
        self._build_tree(tree)
        entry = file_router_config_entry(pages_dir=tree, dirs=dirs)

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            router = RouterFactory.create_backend(entry)
            checked = list(iter_scanned_page_pairs(router))
            routed = [
                pair
                for pages_dir in get_pages_directories(router)
                for pair in router._scan_pages_directory(
                    pages_dir, register_components=False
                )
            ]

        assert checked == routed
        assert checked

    @pytest.mark.parametrize(
        "dirs", [[], ["_drafts"]], ids=["no-dirs", "one-skip-name"]
    )
    def test_the_derived_skip_set_is_the_routers_own(
        self, tmp_path: Path, dirs
    ) -> None:
        tree = tmp_path / "shell"
        tree.mkdir(parents=True)
        entry = file_router_config_entry(pages_dir=tree, dirs=dirs)

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            router = RouterFactory.create_backend(entry)

            assert page_tree_skip_names(router) == router._skip_dir_names

    def test_a_custom_components_dir_moves_both_walks(self, tmp_path: Path) -> None:
        tree = tmp_path / "shell"
        self._build_tree(tree)
        entry = file_router_config_entry(pages_dir=tree)
        components = [
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_widgets",
            }
        ]

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry], "COMPONENT_BACKENDS": components}
        ):
            router = RouterFactory.create_backend(entry)
            routes = [url for url, _page in iter_scanned_page_pairs(router)]

            assert page_tree_skip_names(router) == router._skip_dir_names

        assert "_components/card" in routes


class TestPerRouterCachesKeyOnIdentity:
    """Identically configured routers never read one another's cached scan."""

    def test_a_config_equal_subclass_keeps_its_own_folder_name(
        self, tmp_path: Path
    ) -> None:
        plain = file_router(app_dirs=False, dirs=[tmp_path])
        custom = _CustomFolderRouter(
            file_router_config_entry(app_dirs=False, dirs=[tmp_path])
        )

        assert page_tree_skip_names(plain) == frozenset({"_components"})
        assert page_tree_skip_names(custom) == frozenset({"_widgets"})

    def test_a_config_equal_subclass_keeps_its_own_scan(self, tmp_path: Path) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        (tree / "_widgets").mkdir()
        (tree / "_widgets" / "card").mkdir()
        (tree / "_widgets" / "card" / "page.py").write_text('template = "x"\n')
        plain = file_router(app_dirs=False, dirs=[tree])
        custom = _CustomFolderRouter(
            file_router_config_entry(app_dirs=False, dirs=[tree])
        )

        plain_routes = {url for url, _page in iter_scanned_page_pairs(plain)}
        custom_routes = {url for url, _page in iter_scanned_page_pairs(custom)}

        assert "_widgets/card" in plain_routes
        assert "_widgets/card" not in custom_routes

    def test_an_unhashable_router_is_cached_without_being_hashed(
        self, tmp_path: Path
    ) -> None:
        # A dataclass router carries `__hash__ = None`, which no cache lookup
        # may turn into a traceback out of a check run.
        _write_page(tmp_path, "blog")
        router = _UnhashableRouter(tree=tmp_path)

        assert page_tree_skip_names(router) == frozenset()
        assert [url for url, _page in iter_scanned_page_pairs(router)] == ["blog"]


class TestFailingPageRootsRead:
    """User code that raises or answers the wrong shape costs only its trees."""

    def test_get_page_roots_swallows_and_reports_none(self) -> None:
        assert get_page_roots(RaisingRootsRouter()) == []

    def test_bare_paths_instead_of_page_roots_are_refused(self, tmp_path: Path) -> None:
        # Every reader dereferences `root.path`, so a bare path may not reach one.
        with pytest.raises(PageRootsError) as caught:
            read_page_roots(MalformedRootsRouter([tmp_path]))

        assert "MalformedRootsRouter" in str(caught.value)
        assert "PosixPath" in str(caught.value) or "WindowsPath" in str(caught.value)
        assert caught.value.__cause__ is None

    def test_a_page_root_holding_something_other_than_a_path_is_refused(self) -> None:
        router = _RootTreeRouter(["pages"])

        with pytest.raises(PageRootsError, match=r"str instead of pathlib\.Path"):
            read_page_roots(router)

    def test_the_scanning_seams_survive_a_malformed_router(
        self, tmp_path: Path
    ) -> None:
        router = MalformedRootsRouter([tmp_path])

        assert get_page_roots(router) == []
        assert get_pages_directories(router) == []
        assert list(iter_scanned_page_pairs(router)) == []

    def test_read_page_roots_folds_the_failure_into_one_error(self) -> None:
        # Folded rather than raised raw, so both callers catch it narrowly.
        with pytest.raises(PageRootsError) as caught:
            read_page_roots(RaisingRootsRouter())

        assert "RaisingRootsRouter" in str(caught.value)
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert str(caught.value.__cause__) == "database is down"

    def test_a_healthy_router_raises_nothing(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")

        roots = read_page_roots(_RootTreeRouter([tmp_path]))

        assert [root.path for root in roots] == [tmp_path]

    def test_the_scan_seam_survives_a_failing_router(self) -> None:
        assert list(iter_scanned_page_pairs(RaisingRootsRouter())) == []
