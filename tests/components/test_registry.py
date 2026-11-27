import gc
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.caches import LruCache
from next.components import (
    ComponentInfo,
    ComponentRegistry,
    ComponentScanner,
    ComponentVisibilityResolver,
    component_extra_roots_from_config,
)
from next.components.defer import DeferDeclarations
from next.components.info import (
    BoundaryFiles,
    boundary_files,
    paths_from_component_info,
)
from next.components.modules import ModuleLoader
from next.components.registry import DeferredComponents, ErrorFiles, may_defer
from next.introspect import DeclaringSite


class TestComponentRegistry:
    """ComponentRegistry helpers and dunders."""

    def test_root_mark_clear_iter_contains_len(self, tmp_path: Path) -> None:
        """A registered component is a member until the registry is cleared."""
        reg = ComponentRegistry()
        root = tmp_path.resolve()
        info = ComponentInfo("n", root, "", tmp_path / "n.djx", None, True)
        reg.register(info)
        reg.mark_as_root(root)
        assert reg.is_root(root)
        assert "n" in reg
        assert len(reg) == 1
        assert list(reg) == [info]
        reg.clear()
        assert len(reg) == 0
        assert not reg.is_root(root)

    def test_component_info_resolves_scope_root_and_falls_back_on_oserror(
        self, tmp_path: Path
    ) -> None:
        """`resolved_scope_root` falls back to the raw path when `resolve()` raises."""
        root = tmp_path.resolve()
        info = ComponentInfo("n", root, "", tmp_path / "n.djx", None, True)
        assert info.resolved_scope_root == root

        broken_root = MagicMock(spec=Path)
        broken_root.resolve.side_effect = OSError
        info2 = ComponentInfo("n", broken_root, "", tmp_path / "n.djx", None, True)
        assert info2.resolved_scope_root is broken_root

    def test_a_component_is_named_by_its_name_and_module(self, tmp_path: Path) -> None:
        module = tmp_path / "component.py"
        info = ComponentInfo("card", tmp_path, "", None, module, False)
        assert str(info) == f"component 'card' ({module})"

    def test_contains_is_indexed_by_name(self, tmp_path: Path) -> None:
        """Name lookup does not scan every row."""
        reg = ComponentRegistry()
        root = tmp_path.resolve()
        for i in range(50):
            reg.register(
                ComponentInfo(f"c{i}", root, "", tmp_path / f"{i}.djx", None, True)
            )
        assert "c49" in reg
        assert "missing" not in reg


class TestComponentScanner:
    """ComponentScanner edge cases."""

    def test_scan_oserror_on_iterdir(self, tmp_path: Path) -> None:
        """OSError from ``iterdir`` is swallowed. An empty list is returned."""
        err = OSError("no access")

        def boom() -> None:
            raise err

        directory = MagicMock(spec=Path)
        directory.iterdir = boom
        scanner = ComponentScanner()
        assert scanner.scan_directory(directory, tmp_path, "") == []

    def test_composite_py_only_with_component_string(self, tmp_path: Path) -> None:
        """Folder with only component.py exposing component uses py as template path."""
        d = tmp_path / "widget"
        d.mkdir()
        (d / "component.py").write_text('component = "<span>{{ v }}</span>"\n')
        scanner = ComponentScanner()
        found = scanner.scan_directory(tmp_path, tmp_path, "")
        assert len(found) == 1
        w = found[0]
        assert w.name == "widget"
        assert w.template_path == d / "component.py"

    def test_composite_py_only_without_component_has_no_template(
        self, tmp_path: Path
    ) -> None:
        """A component.py exposing no ``component`` leaves the template unresolved."""
        d = tmp_path / "widget"
        d.mkdir()
        (d / "component.py").write_text("value = 1\n")
        scanner = ComponentScanner()
        found = scanner.scan_directory(tmp_path, tmp_path, "")
        assert len(found) == 1
        assert found[0].template_path is None
        assert found[0].module_path == d / "component.py"

    def test_a_file_that_is_no_djx_is_skipped(self, tmp_path: Path) -> None:
        """Only ``.djx`` files become simple components, other files are passed over."""
        (tmp_path / "notes.txt").write_text("x")
        (tmp_path / "card.djx").write_text("<div/>")
        scanner = ComponentScanner()
        found = scanner.scan_directory(tmp_path, tmp_path, "")
        assert [c.name for c in found] == ["card"]

    def test_subdir_without_component_files_is_ignored(self, tmp_path: Path) -> None:
        """Directories without component.djx or component.py produce no composite."""
        (tmp_path / "empty_dir").mkdir()
        scanner = ComponentScanner()
        assert scanner.scan_directory(tmp_path, tmp_path, "") == []


class TestBoundaryFiles:
    """The `loading.djx` and `error.djx` a composite component holds."""

    def _composite(self, root: Path, *files: str) -> ComponentInfo:
        directory = root / "widget"
        directory.mkdir()
        (directory / "component.djx").write_text("<p>w</p>")
        for name in files:
            (directory / name).write_text("<p>x</p>")
        [info] = ComponentScanner().scan_directory(root, root, "")
        return info

    def test_a_component_with_neither_file_holds_none(self, tmp_path: Path) -> None:
        assert self._composite(tmp_path).files is None

    @pytest.mark.parametrize(
        ("files", "loading", "error"),
        [
            (("loading.djx",), True, False),
            (("error.djx",), False, True),
            (("loading.djx", "error.djx"), True, True),
        ],
    )
    def test_the_scan_records_each_file_present(
        self, tmp_path: Path, files: tuple[str, ...], loading, error
    ) -> None:
        info = self._composite(tmp_path, *files)
        directory = tmp_path / "widget"
        assert info.files == BoundaryFiles(
            directory / "loading.djx" if loading else None,
            directory / "error.djx" if error else None,
        )

    def test_a_directory_named_like_a_file_is_no_boundary_file(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "loading.djx").mkdir()
        assert boundary_files(tmp_path) is None

    def test_a_simple_component_has_no_files(self, tmp_path: Path) -> None:
        (tmp_path / "loading.djx").write_text("<p>x</p>")
        info = ComponentInfo("card", tmp_path, "", tmp_path / "card.djx", None, True)
        assert info.files is None
        assert info.current_files() is None

    def test_a_composite_without_a_source_has_no_files(self, tmp_path: Path) -> None:
        info = ComponentInfo("card", tmp_path, "", None, None, False)
        assert info.current_files() is None

    def test_a_module_only_composite_reads_its_directory(self, tmp_path: Path) -> None:
        (tmp_path / "error.djx").write_text("<p>x</p>")
        info = ComponentInfo(
            "card", tmp_path, "", None, tmp_path / "component.py", False
        )
        assert info.current_files() == BoundaryFiles(None, tmp_path / "error.djx")

    def test_the_watched_paths_include_the_boundary_files(self, tmp_path: Path) -> None:
        info = self._composite(tmp_path, "loading.djx", "error.djx")
        directory = (tmp_path / "widget").resolve()
        assert paths_from_component_info(info) == {
            directory / "component.djx",
            directory / "loading.djx",
            directory / "error.djx",
        }

    def test_the_watched_paths_skip_a_missing_boundary_file(
        self, tmp_path: Path
    ) -> None:
        info = self._composite(tmp_path, "error.djx")
        assert (
            tmp_path / "widget" / "error.djx"
        ).resolve() in paths_from_component_info(info)
        assert len(paths_from_component_info(info)) == 2


class TestRefreshFiles:
    """`ComponentRegistry.refresh_files` swaps in the files on disk now."""

    def _registered(
        self, tmp_path: Path, *names: str
    ) -> tuple[ComponentRegistry, list[ComponentInfo]]:
        for name in names or ("widget",):
            directory = tmp_path / name
            directory.mkdir()
            (directory / "component.djx").write_text("<p>w</p>")
        registry = ComponentRegistry()
        registry.register_many(
            ComponentScanner().scan_directory(tmp_path, tmp_path, "")
        )
        return registry, list(registry.get_all())

    def test_unchanged_files_keep_the_entries_and_the_version(
        self, tmp_path: Path
    ) -> None:
        registry, infos = self._registered(tmp_path)
        version = registry.version
        assert registry.refresh_files() is False
        assert list(registry.get_all()) == infos
        assert registry.version == version

    def test_a_file_that_appears_is_swapped_in(self, tmp_path: Path) -> None:
        registry, [info] = self._registered(tmp_path)
        version = registry.version
        loading = tmp_path / "widget" / "loading.djx"
        loading.write_text("<p>x</p>")
        assert registry.refresh_files() is True
        [fresh] = registry.get_all()
        assert fresh.files == BoundaryFiles(loading, None)
        assert fresh.name == info.name
        assert list(registry) == [fresh]
        assert registry.version == version + 1
        assert registry.refresh_files() is False

    def test_a_file_that_goes_is_swapped_out(self, tmp_path: Path) -> None:
        (tmp_path / "widget").mkdir()
        (tmp_path / "widget" / "error.djx").write_text("<p>x</p>")
        (tmp_path / "widget" / "component.djx").write_text("<p>w</p>")
        registry = ComponentRegistry()
        registry.register_many(
            ComponentScanner().scan_directory(tmp_path, tmp_path, "")
        )
        (tmp_path / "widget" / "error.djx").unlink()
        assert registry.refresh_files() is True
        assert [info.files for info in registry.get_all()] == [None]

    def test_only_the_changed_entry_is_replaced(self, tmp_path: Path) -> None:
        registry, infos = self._registered(tmp_path, "a", "b")
        (tmp_path / "b" / "error.djx").write_text("<p>x</p>")
        registry.refresh_files()
        before = {info.name: info for info in infos}
        after = {info.name: info for info in registry.get_all()}
        assert after["a"] is before["a"]
        assert after["b"] is not before["b"]
        assert after["b"].files is not None
        assert [info.name for info in registry.get_all()] == [i.name for i in infos]

    def test_a_swap_an_earlier_refresh_applied_is_not_applied_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry, _ = self._registered(tmp_path)
        (tmp_path / "widget" / "error.djx").write_text("<p>x</p>")
        real = ComponentInfo.current_files
        raced: list[bool] = []

        def racing(entry: ComponentInfo) -> BoundaryFiles | None:
            if not raced:
                raced.append(False)
                raced[0] = registry.refresh_files()
            return real(entry)

        monkeypatch.setattr(ComponentInfo, "current_files", racing)
        version = registry.version
        assert registry.refresh_files() is False
        assert raced == [True]
        assert registry.version == version + 1
        [fresh] = registry.get_all()
        assert fresh.files is not None

    def test_concurrent_refreshes_agree(self, tmp_path: Path) -> None:
        registry, _ = self._registered(tmp_path, "a", "b", "c")
        for name in ("a", "c"):
            (tmp_path / name / "loading.djx").write_text("<p>x</p>")
        barrier = threading.Barrier(8)
        answers: list[bool] = []

        def refresh() -> None:
            barrier.wait()
            answers.append(registry.refresh_files())

        threads = [threading.Thread(target=refresh) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert answers.count(True) >= 1
        assert len(registry) == 3
        assert {info.name: info.files is not None for info in registry} == {
            "a": True,
            "b": False,
            "c": True,
        }
        assert sorted(registry._by_name) == ["a", "b", "c"]


class TestErrorFiles:
    """Every live registry reports whether a component of it holds an `error.djx`."""

    @pytest.fixture()
    def fresh_deferred_components(self, monkeypatch: pytest.MonkeyPatch) -> ErrorFiles:
        fresh = ErrorFiles()
        monkeypatch.setattr("next.components.registry.error_files", fresh)
        return fresh

    def _info(self, root: Path, name: str, *, error: bool) -> ComponentInfo:
        files = BoundaryFiles(None, root / name / "error.djx") if error else None
        return ComponentInfo(
            name, root, "", root / name / "component.djx", None, True, files=files
        )

    def test_a_fresh_tracker_holds_none(self) -> None:
        assert ErrorFiles().held is False

    def test_a_count_holds_until_it_drops_to_zero(self) -> None:
        tracker = ErrorFiles()
        registry = ComponentRegistry()
        tracker.note(registry, 2)
        assert tracker.held is True
        tracker.note(registry, 0)
        assert tracker.held is False

    def test_a_forgotten_registry_holds_none(self) -> None:
        tracker = ErrorFiles()
        registry = ComponentRegistry()
        tracker.note(registry, 1)
        tracker.forget(id(registry))
        assert tracker.held is False

    def test_a_component_without_an_error_file_holds_none(
        self, fresh_deferred_components: ErrorFiles, tmp_path: Path
    ) -> None:
        registry = ComponentRegistry()
        registry.register(self._info(tmp_path, "a", error=False))
        registry.register_many([self._info(tmp_path, "b", error=False)])
        assert fresh_deferred_components.held is False

    def test_register_and_clear(
        self, fresh_deferred_components: ErrorFiles, tmp_path: Path
    ) -> None:
        registry = ComponentRegistry()
        registry.register(self._info(tmp_path, "a", error=True))
        assert fresh_deferred_components.held is True
        registry.clear()
        assert fresh_deferred_components.held is False

    def test_register_many_counts_every_error_file(
        self, fresh_deferred_components: ErrorFiles, tmp_path: Path
    ) -> None:
        registry = ComponentRegistry()
        registry.register_many(
            [
                self._info(tmp_path, "a", error=True),
                self._info(tmp_path, "b", error=False),
            ]
        )
        assert fresh_deferred_components.held is True
        assert registry._error_files == 1

    def test_refresh_follows_the_files_on_disk(
        self, fresh_deferred_components: ErrorFiles, tmp_path: Path
    ) -> None:
        (tmp_path / "widget").mkdir()
        (tmp_path / "widget" / "component.djx").write_text("<p>w</p>")
        registry = ComponentRegistry()
        registry.register_many(
            ComponentScanner().scan_directory(tmp_path, tmp_path, "")
        )
        assert fresh_deferred_components.held is False
        error = tmp_path / "widget" / "error.djx"
        error.write_text("<p>x</p>")
        assert registry.refresh_files() is True
        assert fresh_deferred_components.held is True
        error.unlink()
        assert registry.refresh_files() is True
        assert fresh_deferred_components.held is False

    def test_a_registry_that_goes_away_holds_none(
        self, fresh_deferred_components: ErrorFiles, tmp_path: Path
    ) -> None:
        registry = ComponentRegistry()
        registry.register(self._info(tmp_path, "a", error=True))
        assert fresh_deferred_components.held is True
        del registry
        gc.collect()
        assert fresh_deferred_components.held is False


class TestComponentExtraRootsFromConfig:
    """``component_extra_roots_from_config`` accepts several ``DIRS`` forms."""

    def test_dirs_tuple_and_path_instances(self, tmp_path: Path) -> None:
        """``DIRS`` accepts tuple and Path elements."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        r1 = component_extra_roots_from_config({"DIRS": (a, b)})
        assert len(r1) == 2
        r2 = component_extra_roots_from_config(
            {"DIRS": [str(b.resolve()), Path(str(a))]}
        )
        assert {Path(p).resolve() for p in r2} == {a.resolve(), b.resolve()}
        missing = tmp_path / "nope"
        assert not missing.exists()
        r3 = component_extra_roots_from_config(
            {"DIRS": [str(a.resolve()), str(missing)]}
        )
        assert r3 == [a.resolve()]

        assert component_extra_roots_from_config({"DIRS": [str(missing)]}) == []

    @pytest.mark.parametrize(
        "dirs",
        [pytest.param(5, id="scalar"), pytest.param("src/components", id="string")],
    )
    def test_dirs_that_is_no_sequence_of_trees(self, dirs: object) -> None:
        """A scalar and a string alike answer ``ImproperlyConfigured``."""
        with pytest.raises(ImproperlyConfigured, match="sequence of trees"):
            component_extra_roots_from_config({"DIRS": dirs})


class TestComponentVisibilityResolver:
    """Visibility scoring and path cache."""

    def test_not_visible_when_outside_scope(self, tmp_path: Path) -> None:
        """Template path outside scope_root yields no visible scoped components."""
        pages = tmp_path / "pages"
        about = pages / "about"
        comp_dir = about / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "c.djx").write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo("c", pages.resolve(), "about", comp_dir / "c.djx", None, True)
        )
        resolver = ComponentVisibilityResolver(reg)
        outside = tmp_path / "elsewhere" / "t.djx"
        outside.parent.mkdir(parents=True)
        assert resolver.resolve_visible(outside) == {}

    def test_second_resolve_returns_the_cached_result_object(
        self, tmp_path: Path
    ) -> None:
        """The second resolve hands back the very mapping built by the first."""
        pages = tmp_path / "pages"
        tmpl = pages / "home.djx"
        tmpl.parent.mkdir(parents=True)
        tmpl.write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo(
                "c", pages.resolve(), "", pages / "_components" / "c.djx", None, True
            )
        )
        (pages / "_components").mkdir()
        (pages / "_components" / "c.djx").write_text("y")
        res = ComponentVisibilityResolver(reg)
        r1 = res.resolve_visible(tmpl)
        r2 = res.resolve_visible(tmpl)
        assert r2 is r1
        assert r1["c"].name == "c"

    def test_scope_index_reused_for_second_template_path(self, tmp_path: Path) -> None:
        """Second template path does not rebuild the per-root index."""
        pages = tmp_path / "pages"
        comp_dir = pages / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "c.djx").write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo("c", pages.resolve(), "about", comp_dir / "c.djx", None, True)
        )
        res = ComponentVisibilityResolver(reg)
        t1 = pages / "about" / "a.djx"
        t2 = pages / "about" / "b.djx"
        t1.parent.mkdir(parents=True, exist_ok=True)
        t1.write_text("z")
        t2.write_text("z")
        assert "c" in res.resolve_visible(t1)
        index_after_first = res._scope_index
        version_after_first = res._scope_index_registry_version

        assert "c" in res.resolve_visible(t2)
        assert res._scope_index is index_after_first
        assert res._scope_index_registry_version == version_after_first

    def test_scope_index_rebuilt_after_registry_changes(self, tmp_path: Path) -> None:
        """A registry mutation invalidates the scope index and the result caches."""
        pages = tmp_path / "pages"
        comp_dir = pages / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "c.djx").write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo("c", pages.resolve(), "about", comp_dir / "c.djx", None, True)
        )
        res = ComponentVisibilityResolver(reg)
        tmpl = pages / "about" / "a.djx"
        tmpl.write_text("z")
        first = res.resolve_visible(tmpl)
        index_after_first = res._scope_index
        assert set(first) == {"c"}

        reg.register(
            ComponentInfo("d", pages.resolve(), "about", comp_dir / "d.djx", None, True)
        )
        second = res.resolve_visible(tmpl)
        assert res._scope_index is not index_after_first
        assert set(second) == {"c", "d"}

    def test_global_root_component_with_scope_relative_not_visible_far_away(
        self, tmp_path: Path
    ) -> None:
        """Marked global root still checks scope path when ``scope_relative`` is set."""
        root = tmp_path / "global"
        root.mkdir()
        reg = ComponentRegistry()
        reg.mark_as_root(root.resolve())
        reg.register(
            ComponentInfo("x", root.resolve(), "onlyhere", root / "x.djx", None, True)
        )
        res = ComponentVisibilityResolver(reg)
        outsider = tmp_path / "else" / "t.djx"
        outsider.parent.mkdir()
        assert res.resolve_visible(outsider) == {}

    def test_compute_relative_parts_valueerror(self, tmp_path: Path) -> None:
        """Paths on different branches return ``None`` from the helper."""
        reg = ComponentRegistry()
        res = ComponentVisibilityResolver(reg)
        assert (
            res._compute_relative_parts(tmp_path / "a" / "t.djx", tmp_path / "b")
            is None
        )

    def test_compute_relative_parts_template_at_scope_root(
        self, tmp_path: Path
    ) -> None:
        """Template directory equals scope_root yields a single empty route prefix."""
        reg = ComponentRegistry()
        res = ComponentVisibilityResolver(reg)
        pages = tmp_path / "pages"
        pages.mkdir()
        tmpl = pages / "template.djx"
        tmpl.write_text("x")
        parts = res._compute_relative_parts(tmpl.resolve(), pages.resolve())
        assert parts == [""]

    def test_result_and_path_cache_evict_oldest_when_full(self, tmp_path: Path) -> None:
        """Exceeding the LRU size evicts the oldest entries for both caches."""
        reg = ComponentRegistry()
        scope_root = (tmp_path / "scope").resolve()
        scope_root.mkdir()
        sub = scope_root / "area"
        sub.mkdir()
        # A shared scope_root makes `resolve_visible()` hit the parts cache twice.
        reg.register(
            ComponentInfo("c1", scope_root, "area", sub / "c1.djx", None, True)
        )
        reg.register(
            ComponentInfo("c2", scope_root, "area", sub / "c2.djx", None, True)
        )

        res = ComponentVisibilityResolver(reg)
        res._result_cache = LruCache(2)
        res._path_cache = LruCache(2)
        paths = [sub / f"t{i}.djx" for i in range(3)]
        for p in paths:
            p.write_text("x")
            res.resolve_visible(p)

        assert paths[0].resolve() not in res._result_cache
        assert (paths[0].resolve(), scope_root) not in res._path_cache
        assert paths[-1].resolve() in res._result_cache

    def _resolve_same_name_from_two_roots(
        self, base: Path, first: str, second: str
    ) -> ComponentInfo:
        """Register a same-named component from two DIRS roots in given order."""
        reg = ComponentRegistry()
        for name in (first, second):
            root = (base / name).resolve()
            root.mkdir(parents=True)
            info = ComponentInfo("button", root, "", root / "button.djx", None, True)
            reg.register(info)
            reg.mark_as_root(root)
        tmpl = base / "page" / "home.djx"
        tmpl.parent.mkdir(parents=True)
        tmpl.write_text("x")
        resolved = ComponentVisibilityResolver(reg).resolve_visible(tmpl)
        return resolved["button"]

    def test_equal_score_tie_breaks_on_registration_order(self, tmp_path: Path) -> None:
        """Two equal-score same-origin DIRS roots resolve to the one registered first."""
        winner = self._resolve_same_name_from_two_roots(
            tmp_path / "case1", "z_root", "a_root"
        )
        assert winner.scope_root == (tmp_path / "case1" / "z_root").resolve()

        other = self._resolve_same_name_from_two_roots(
            tmp_path / "case2", "a_root", "z_root"
        )
        assert other.scope_root == (tmp_path / "case2" / "a_root").resolve()

    def _register_page_tree_and_dirs_button(
        self, base: Path
    ) -> tuple[ComponentRegistry, Path, Path]:
        """Register a DIRS `button` then a page-tree `button` under `base`."""
        pages = (base / "pages").resolve()
        dirs_root = (base / "shared").resolve()
        (pages / "_components").mkdir(parents=True)
        dirs_root.mkdir(parents=True)
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo("button", dirs_root, "", dirs_root / "button.djx", None, True)
        )
        reg.mark_as_root(dirs_root)
        reg.register(
            ComponentInfo(
                "button", pages, "", pages / "_components" / "button.djx", None, True
            )
        )
        return reg, pages, dirs_root

    def test_page_tree_component_shadows_dirs_root_at_equal_score(
        self, tmp_path: Path
    ) -> None:
        """A project-local page-tree component wins over a same-name DIRS root."""
        reg, pages, _dirs_root = self._register_page_tree_and_dirs_button(tmp_path)
        tmpl = pages / "home.djx"
        tmpl.write_text("x")
        resolved = ComponentVisibilityResolver(reg).resolve_visible(tmpl)
        assert resolved["button"].scope_root == pages

    def test_dirs_root_button_visible_outside_page_tree(self, tmp_path: Path) -> None:
        """A template outside the page tree still sees the shared DIRS button."""
        reg, _pages, dirs_root = self._register_page_tree_and_dirs_button(tmp_path)
        outside = tmp_path / "elsewhere" / "t.djx"
        outside.parent.mkdir(parents=True)
        outside.write_text("x")
        resolved = ComponentVisibilityResolver(reg).resolve_visible(outside)
        assert resolved["button"].scope_root == dirs_root


class TestRootsAreMarkedUnderBothSpellings:
    """A root named through a symlink still answers for the path a scan resolved."""

    def test_the_resolved_spelling_of_a_marked_root_is_a_root(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real"
        real.mkdir()
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        reg = ComponentRegistry()

        reg.mark_as_root(linked)

        assert reg.is_root(linked)
        assert reg.is_root(real.resolve())

    def test_both_spellings_reach_the_global_root_set(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        reg = ComponentRegistry()

        reg.mark_as_root(linked)

        assert {linked, real.resolve()} <= reg.global_roots()

    def test_a_component_registered_under_the_link_resolves_everywhere(
        self, tmp_path: Path
    ) -> None:
        """The score reads the resolved root, which only the second mark makes a root."""
        real = tmp_path / "real"
        real.mkdir()
        (real / "card.djx").write_text("<p>c</p>")
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        reg = ComponentRegistry()
        reg.mark_as_root(linked)
        reg.register(ComponentInfo("card", linked, "", linked / "card.djx", None, True))
        outsider = tmp_path / "elsewhere" / "page.djx"
        outsider.parent.mkdir()
        outsider.write_text("x")

        assert set(ComponentVisibilityResolver(reg).resolve_visible(outsider)) == {
            "card"
        }

    def test_a_root_that_was_never_marked_scopes_its_components_to_its_tree(
        self, tmp_path: Path
    ) -> None:
        """An unmarked root is a page tree, so its components stay below it."""
        tree = (tmp_path / "pages").resolve()
        tree.mkdir()
        (tree / "card.djx").write_text("<p>c</p>")
        reg = ComponentRegistry()
        reg.register(ComponentInfo("card", tree, "", tree / "card.djx", None, True))
        res = ComponentVisibilityResolver(reg)
        inside = tree / "home.djx"
        inside.write_text("x")
        outside = tmp_path / "elsewhere" / "page.djx"
        outside.parent.mkdir()
        outside.write_text("x")

        assert set(res.resolve_visible(inside)) == {"card"}
        assert res.resolve_visible(outside) == {}


class TestTheResolvedPathMemoKeysOnTheCallerSpelling:
    """A render repeating one unresolved path keeps hitting the memo it seeded.

    Keying on the resolved path made the caller's spelling miss forever.
    """

    def _tree_behind_a_link(self, tmp_path: Path) -> tuple[ComponentRegistry, Path]:
        real = tmp_path / "real"
        real.mkdir()
        (real / "card.djx").write_text("<p>c</p>")
        linked = tmp_path / "linked"
        linked.symlink_to(real, target_is_directory=True)
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo("card", real.resolve(), "", real / "card.djx", None, True)
        )
        return reg, linked

    def test_a_repeat_of_one_spelling_answers_without_resolving_again(
        self, tmp_path: Path
    ) -> None:
        # The link moves between calls, so only the memo answers the second `resolve()`.
        reg, linked = self._tree_behind_a_link(tmp_path)
        res = ComponentVisibilityResolver(reg)
        template = linked / "home.djx"
        template.write_text("x")

        first = res.resolve_visible(template)
        (tmp_path / "linked").unlink()
        second = res.resolve_visible(template)

        assert set(first) == {"card"}
        assert second is first

    def test_a_registration_drops_the_memo_along_with_the_results(
        self, tmp_path: Path
    ) -> None:
        """A version bump clears every memo, so a new component is never hidden."""
        reg, linked = self._tree_behind_a_link(tmp_path)
        res = ComponentVisibilityResolver(reg)
        template = linked / "home.djx"
        template.write_text("x")
        assert set(res.resolve_visible(template)) == {"card"}

        real = (tmp_path / "real").resolve()
        (real / "badge.djx").write_text("<p>b</p>")
        reg.register(ComponentInfo("badge", real, "", real / "badge.djx", None, True))

        assert set(res.resolve_visible(template)) == {"badge", "card"}


class TestDeferredComponents:
    """Whether any component can defer, by its files, its module or a call site."""

    def _info(
        self, root: Path, name: str, *, loading: bool = False, module: bool = False
    ) -> ComponentInfo:
        files = BoundaryFiles(root / name / "loading.djx", None) if loading else None
        module_path = root / name / "component.py" if module else None
        return ComponentInfo(
            name,
            root,
            "",
            root / name / "component.djx",
            module_path,
            True,
            files=files,
        )

    def test_a_loading_file_holds_until_the_registry_clears(
        self, fresh_deferred_components: DeferredComponents, tmp_path: Path
    ) -> None:
        registry = ComponentRegistry()
        registry.register(self._info(tmp_path, "plain"))
        assert fresh_deferred_components.held is False
        registry.register(self._info(tmp_path, "card", loading=True))
        assert fresh_deferred_components.held is True
        registry.clear()
        assert fresh_deferred_components.held is False

    def test_a_module_declaring_a_mode_holds(
        self, fresh_deferred_components: DeferredComponents, tmp_path: Path, monkeypatch
    ) -> None:
        store = DeferDeclarations()
        store.defer(
            DeclaringSite(tmp_path / "card" / "component.py", True, {}),
            {"mode": "load"},
        )
        store.props(DeclaringSite(tmp_path / "bare" / "component.py", True, {}), ())
        monkeypatch.setattr("next.components.registry.defer_declarations", store)
        registry = ComponentRegistry()
        registry.register_many([self._info(tmp_path, "bare", module=True)])
        assert fresh_deferred_components.held is False
        registry.register_many([self._info(tmp_path, "card", module=True)])
        assert fresh_deferred_components.held is True
        assert registry._deferring == 1

    def test_a_loading_file_beside_a_declared_mode_counts_once(
        self, fresh_deferred_components: DeferredComponents, tmp_path: Path, monkeypatch
    ) -> None:
        store = DeferDeclarations()
        store.defer(
            DeclaringSite(tmp_path / "card" / "component.py", True, {}),
            {"mode": "load"},
        )
        monkeypatch.setattr("next.components.registry.defer_declarations", store)
        registry = ComponentRegistry()
        registry.register(self._info(tmp_path, "card", loading=True, module=True))
        assert fresh_deferred_components.held is True
        assert registry._deferring == 1

    def test_may_defer_raises_the_flag_for_good(
        self, fresh_deferred_components: DeferredComponents
    ) -> None:
        may_defer()
        may_defer()
        assert (fresh_deferred_components.held, fresh_deferred_components.declared) == (
            True,
            True,
        )

    def test_a_committed_mode_raises_the_flag_before_any_render(
        self, fresh_deferred_components: DeferredComponents, tmp_path: Path
    ) -> None:
        module_path = tmp_path / "card" / "component.py"
        module_path.parent.mkdir()
        module_path.write_text(
            "from next.components import component\ncomponent.defer()\n"
        )
        ModuleLoader().load(module_path)
        assert fresh_deferred_components.held is True

    def test_a_declaration_holds_for_good(
        self, fresh_deferred_components: DeferredComponents
    ) -> None:
        registry = ComponentRegistry()
        fresh_deferred_components.declare()
        fresh_deferred_components.note(registry, 0)
        assert fresh_deferred_components.held is True
        assert fresh_deferred_components.declared is True

    def test_refresh_follows_a_new_loading_file(
        self, fresh_deferred_components: DeferredComponents, tmp_path: Path
    ) -> None:
        (tmp_path / "widget").mkdir()
        (tmp_path / "widget" / "component.djx").write_text("<p>w</p>")
        registry = ComponentRegistry()
        registry.register_many(
            ComponentScanner().scan_directory(tmp_path, tmp_path, "")
        )
        assert fresh_deferred_components.held is False
        (tmp_path / "widget" / "loading.djx").write_text("<p>.</p>")
        assert registry.refresh_files() is True
        assert fresh_deferred_components.held is True
