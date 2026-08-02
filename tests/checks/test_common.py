from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from django.core.checks import run_checks
from django.core.checks.registry import registry
from django.test import override_settings

from next.checks import NEXT, register_all
from next.checks.common import (
    PageRootsError,
    RegistrationSubject,
    get_components_manager,
    get_page_roots,
    get_pages_directories,
    get_router_manager,
    iter_page_tree_component_folders,
    iter_scanned_page_pairs,
    page_tree_skip_names,
    read_page_roots,
    registration_file_errors,
    reset_components_manager_cache,
    reset_router_manager_cache,
)
from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.urls import (
    FileRouterBackend,
    PageRoot,
    RouterBackend,
    RouterFactory,
    checks as urls_checks,
)
from next.urls.checks import check_reverse_name_collisions, check_url_patterns
from next.urls.dispatcher import scan_pages_tree
from next.utils import walk_page_tree
from tests.support import (
    MalformedRootsRouter,
    OddComponentsNameRouter,
    RaisingComponentsRouter,
    RaisingRootsRouter,
    file_router_config_entry,
    patch_checks_router_manager_with_routers,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_manager_caches() -> Iterator[None]:
    reset_router_manager_cache()
    reset_components_manager_cache()
    urls_checks.reset_collected_patterns_cache()
    yield
    reset_router_manager_cache()
    reset_components_manager_cache()
    urls_checks.reset_collected_patterns_cache()


def _labelled_root(index: int, tree: Path) -> PageRoot:
    return PageRoot(path=tree, label="Root" if index == 0 else f"Root ({tree})")


def _write_page(tree: Path, route: str) -> Path:
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text('template = "ok"\n')
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

    def components_folder_name(self) -> str | None:
        return "_widgets"


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
    with patch("next.checks.common.walk_page_tree", wraps=walk_page_tree) as spy:
        yield spy


def _walked_trees(spy: MagicMock) -> list[Path]:
    """Return the root of every walk the spy recorded, in order."""
    return [call.args[0] for call in spy.call_args_list]


class TestRouterManagerCache:
    """`get_router_manager` reuses one manager per check run."""

    def test_built_once_across_repeated_calls(self) -> None:
        with patch("next.urls.RouterManager") as mock_cls:
            first = get_router_manager()
            second = get_router_manager()
            third = get_router_manager()
        assert first is second is third
        assert mock_cls.call_count == 1
        assert mock_cls.return_value.reload.call_count == 1

    def test_explicit_reset_forces_rebuild(self) -> None:
        with patch("next.urls.RouterManager") as mock_cls:
            get_router_manager()
            reset_router_manager_cache()
            get_router_manager()
        assert mock_cls.call_count == 2
        assert mock_cls.return_value.reload.call_count == 2

    def test_settings_reloaded_signal_resets_cache(self) -> None:
        with patch("next.urls.RouterManager") as mock_cls:
            get_router_manager()
            settings_reloaded.send(sender=None)
            get_router_manager()
        assert mock_cls.call_count == 2

    def test_init_error_result_is_cached(self) -> None:
        with patch("next.urls.RouterManager", side_effect=ImportError("boom")):
            manager, errors = get_router_manager()
            second_manager, second_errors = get_router_manager()
        assert manager is None
        assert second_manager is None
        assert errors is second_errors
        assert errors[0].id == "next.E007"


class TestComponentsManagerCache:
    """`get_components_manager` reuses one manager per check run."""

    def test_built_once_across_repeated_calls(self) -> None:
        with patch("next.components.manager.ComponentsManager") as mock_cls:
            first = get_components_manager()
            second = get_components_manager()
            third = get_components_manager()
        assert first is second is third
        assert mock_cls.call_count == 1
        assert mock_cls.return_value.reload.call_count == 1

    def test_explicit_reset_forces_rebuild(self) -> None:
        with patch("next.components.manager.ComponentsManager") as mock_cls:
            get_components_manager()
            reset_components_manager_cache()
            get_components_manager()
        assert mock_cls.call_count == 2
        assert mock_cls.return_value.reload.call_count == 2

    def test_settings_reloaded_signal_resets_cache(self) -> None:
        with patch("next.components.manager.ComponentsManager") as mock_cls:
            get_components_manager()
            settings_reloaded.send(sender=None)
            get_components_manager()
        assert mock_cls.call_count == 2


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
        # An app tree also listed in DIRS is routed twice for real, so the
        # roots keep both entries for the URL checks to compare, while the
        # scan that feeds the page checks walks the tree once.
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
    """The skip set comes from the router contract and from `PAGE_BACKENDS`."""

    def test_a_backend_naming_no_components_folder_skips_only_dirs_names(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [file_router_config_entry(dirs=["api"])]}
        ):
            next_framework_settings.reload()
            assert page_tree_skip_names(_RootTreeRouter([])) == frozenset({"api"})

    def test_the_components_folder_of_the_backend_joins_the_skip_set(
        self, tmp_path: Path
    ) -> None:
        router = FileRouterBackend(components_folder_name="widgets")

        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [file_router_config_entry(pages_dir=tmp_path)]
            }
        ):
            next_framework_settings.reload()
            assert page_tree_skip_names(router) == frozenset({"widgets"})

    def test_a_dirs_entry_naming_a_real_directory_is_a_root_not_a_skip_name(
        self, tmp_path: Path
    ) -> None:
        # The classification is the router's own, so an existing directory is a
        # page root and never a name the walk refuses.
        (tmp_path / "shell").mkdir()
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    file_router_config_entry(dirs=[str(tmp_path / "shell")])
                ]
            }
        ):
            next_framework_settings.reload()
            assert page_tree_skip_names(_RootTreeRouter([])) == frozenset()

    def test_every_entry_contributes_its_skip_names(self) -> None:
        # A name that is no route for one file router is no route for the
        # project, so the walk refuses the union rather than picking an entry.
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    file_router_config_entry(dirs=["api"]),
                    file_router_config_entry(dirs=["_drafts"]),
                ]
            }
        ):
            next_framework_settings.reload()
            names = page_tree_skip_names(_RootTreeRouter([]))

        assert names == frozenset({"api", "_drafts"})

    def test_a_malformed_page_backends_setting_costs_no_skip_name(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": "nonsense"}):
            next_framework_settings.reload()
            assert page_tree_skip_names(_RootTreeRouter([])) == frozenset()

    def test_a_malformed_dirs_entry_costs_no_skip_name(self) -> None:
        entry = file_router_config_entry()
        entry["DIRS"] = "api"
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry, "nonsense"]}):
            next_framework_settings.reload()
            assert page_tree_skip_names(_RootTreeRouter([])) == frozenset()

    def test_a_raising_components_folder_name_costs_only_its_skip_name(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [file_router_config_entry(dirs=["api"])]}
        ):
            next_framework_settings.reload()
            names = page_tree_skip_names(RaisingComponentsRouter([]))

        assert names == frozenset({"api"})

    def test_a_malformed_components_folder_name_costs_only_its_skip_name(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [file_router_config_entry(dirs=["api"])]}
        ):
            next_framework_settings.reload()
            names = page_tree_skip_names(OddComponentsNameRouter([]))

        assert names == frozenset({"api"})

    def test_a_raising_router_is_asked_for_its_folder_name_once_per_run(
        self, tmp_path: Path
    ) -> None:
        # Three checks ask for the same name, and a failing router would
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
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[tree])

        found = sorted(iter_page_tree_component_folders(router))

        assert found == sorted([(top, tree, ""), (nested, tree, "blog")])

    def test_a_folder_under_a_skipped_directory_is_not_reached(
        self, tmp_path: Path
    ) -> None:
        # The walk never enters `_drafts`, so the router never registers what
        # sits under it and neither may the check.
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        self._write_component(tree / "_drafts" / "_components")
        entry = file_router_config_entry(pages_dir=tree, dirs=["_drafts"])

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            next_framework_settings.reload()
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
        router = FileRouterBackend(
            app_dirs=False,
            extra_root_paths=[tree],
            skip_dir_names=frozenset({"widgets"}),
            components_folder_name="widgets",
        )

        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [
                    file_router_config_entry(pages_dir=tree, dirs=["widgets"])
                ]
            }
        ):
            next_framework_settings.reload()
            assert list(iter_page_tree_component_folders(router)) == [
                (widgets, tree, "")
            ]

    def test_pages_and_folders_come_from_one_walk(self, tmp_path: Path) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        self._write_component(tree / "_components")
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[tree])

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
            next_framework_settings.reload()
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
        # The names the checks refuse are the names the file router refuses,
        # so the two walks cannot diverge.
        tree = tmp_path / "shell"
        tree.mkdir(parents=True)
        entry = file_router_config_entry(pages_dir=tree, dirs=dirs)

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            next_framework_settings.reload()
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
            next_framework_settings.reload()
            router = RouterFactory.create_backend(entry)
            routes = [url for url, _page in iter_scanned_page_pairs(router)]

            assert page_tree_skip_names(router) == router._skip_dir_names

        assert "_components/card" in routes


class TestPerRouterCachesKeyOnIdentity:
    """Config equality never makes one router read another one's cached scan."""

    def test_a_config_equal_subclass_keeps_its_own_folder_name(
        self, tmp_path: Path
    ) -> None:
        # `FileRouterBackend.__eq__` compares configuration, so a subclass with
        # the same settings is `==` to the base while naming another folder.
        plain = FileRouterBackend(app_dirs=False, extra_root_paths=[tmp_path])
        custom = _CustomFolderRouter(app_dirs=False, extra_root_paths=[tmp_path])
        assert plain == custom

        assert page_tree_skip_names(plain) == frozenset({"_components"})
        assert page_tree_skip_names(custom) == frozenset({"_widgets"})

    def test_a_config_equal_subclass_keeps_its_own_scan(self, tmp_path: Path) -> None:
        tree = tmp_path / "shell"
        _write_page(tree, "blog")
        (tree / "_widgets").mkdir()
        (tree / "_widgets" / "card").mkdir()
        (tree / "_widgets" / "card" / "page.py").write_text('template = "x"\n')
        plain = FileRouterBackend(app_dirs=False, extra_root_paths=[tree])
        custom = _CustomFolderRouter(app_dirs=False, extra_root_paths=[tree])

        plain_routes = {url for url, _page in iter_scanned_page_pairs(plain)}
        custom_routes = {url for url, _page in iter_scanned_page_pairs(custom)}

        assert "_widgets/card" in plain_routes
        assert "_widgets/card" not in custom_routes

    def test_an_unhashable_router_is_read_rather_than_cached(
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
        # Every reader dereferences `root.path`, so a bare path may not reach
        # one of them.
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
        # Folded rather than propagated raw, so both callers catch it narrowly
        # and the cause still reaches the report.
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


class TestCollectAllPatternsDedup:
    """The two URL checks share one collection walk within a run."""

    def test_two_url_checks_collect_once_with_stable_messages(
        self, tmp_path: Path
    ) -> None:
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        _write_page(tree_a, "blog")
        _write_page(tree_b, "blog")
        router = _RootTreeRouter(root_trees=[tree_a, tree_b])

        with (
            patch_checks_router_manager_with_routers(routers=[router]),
            patch(
                "next.urls.checks._collect_all_patterns_uncached",
                wraps=urls_checks._collect_all_patterns_uncached,
            ) as spy,
        ):
            memo_url = check_url_patterns(None)
            memo_rev = check_reverse_name_collisions(None)

        assert spy.call_count == 1
        assert any(m.id == "next.E015" for m in memo_url)

        urls_checks.reset_collected_patterns_cache()
        with patch_checks_router_manager_with_routers(routers=[router]):
            control_url = check_url_patterns(None)
            urls_checks.reset_collected_patterns_cache()
            control_rev = check_reverse_name_collisions(None)

        assert [(m.id, m.msg) for m in memo_url] == [(m.id, m.msg) for m in control_url]
        assert [(m.id, m.msg) for m in memo_rev] == [(m.id, m.msg) for m in control_rev]


class TestRegisterAll:
    """`register_all` keeps the registered check set stable without server checks."""

    def test_register_all_registers_same_check_set(self) -> None:
        before = {
            getattr(check, "__name__", None) for check in registry.registered_checks
        }
        register_all()
        after = {
            getattr(check, "__name__", None) for check in registry.registered_checks
        }
        assert after == before
        assert len(after) == len(before)


class TestNextTag:
    """The `next` tag selects only `next-dj` checks for `manage.py check`."""

    def test_next_tag_runs_next_checks(self) -> None:
        register_all()
        with override_settings(NEXT_FRAMEWORK={"__unknown_top_level__": True}):
            messages = run_checks(tags=[NEXT])
        assert messages
        assert any(message.id == "next.E035" for message in messages)
        assert all(message.id.startswith("next.") for message in messages)

    def test_unregistered_tag_runs_nothing(self) -> None:
        register_all()
        assert run_checks(tags=["__not_a_real_tag__"]) == []

    def test_no_next_check_carries_compatibility_tag(self) -> None:
        register_all()
        next_checks = [
            check
            for check in registry.registered_checks
            if getattr(check, "__module__", "").startswith("next.")
        ]
        assert next_checks
        assert all("compatibility" not in check.tags for check in next_checks)


class TestRegistrationFileErrors:
    """`registration_file_errors` turns registry state into check messages."""

    subject = RegistrationSubject(
        decorator="@context",
        anchor_name="page.py",
        render="page render",
        code="next.E074",
    )

    def test_anchor_file_registration_reports_nothing(self, tmp_path: Path) -> None:
        errors = registration_file_errors(
            self.subject,
            registrations={tmp_path / "page.py": ("greeting",)},
            misattributed=[],
        )
        assert errors == []

    def test_helper_file_registration_reports_the_dead_binding(
        self, tmp_path: Path
    ) -> None:
        helper = tmp_path / "helpers.py"
        errors = registration_file_errors(
            self.subject,
            registrations={helper: ("greeting", "farewell")},
            misattributed=[],
        )
        assert [e.id for e in errors] == ["next.E074"]
        assert "farewell, greeting" in errors[0].msg

    def test_misattributed_name_is_not_repeated_by_the_helper_arm(
        self, tmp_path: Path
    ) -> None:
        helper = tmp_path / "helpers.py"
        page_file = tmp_path / "page.py"
        errors = registration_file_errors(
            self.subject,
            registrations={helper: ("greeting", "farewell")},
            misattributed=[(page_file, helper, "greeting")],
        )
        assert [e.id for e in errors] == ["next.E074", "next.E074"]
        assert "greeting" in errors[0].msg
        assert errors[1].msg.count("farewell") == 1
        assert "greeting" not in errors[1].msg

    def test_fully_misattributed_helper_reports_once(self, tmp_path: Path) -> None:
        helper = tmp_path / "helpers.py"
        page_file = tmp_path / "page.py"
        errors = registration_file_errors(
            self.subject,
            registrations={helper: ("greeting",)},
            misattributed=[(page_file, helper, "greeting")],
        )
        assert len(errors) == 1
        assert str(page_file) in errors[0].msg

    def test_records_are_ordered_by_their_paths(self, tmp_path: Path) -> None:
        helper = tmp_path / "helpers.py"
        first = tmp_path / "a" / "page.py"
        second = tmp_path / "b" / "page.py"
        errors = registration_file_errors(
            self.subject,
            registrations={},
            misattributed=[(second, helper, "later"), (first, helper, "earlier")],
        )
        assert [str(first) in errors[0].msg, str(second) in errors[1].msg] == [
            True,
            True,
        ]
