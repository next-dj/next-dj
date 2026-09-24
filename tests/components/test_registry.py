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

    def test_result_and_path_cache_evict_oldest_when_full(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Exceeding the LRU size evicts the oldest entries for both caches."""
        reg = ComponentRegistry()
        scope_root = (tmp_path / "scope").resolve()
        scope_root.mkdir()
        sub = scope_root / "area"
        sub.mkdir()
        # Two components share a scope_root, so one `resolve_visible()` queries
        # `_get_relative_parts_cached` twice and the second hit moves the entry.
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

    Keying the memo on the resolved path left the caller's own spelling missing
    forever, so every render paid the `resolve()` again.
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
        # The link goes between the two calls, so a second `resolve()` would land
        # somewhere else and the memo is the only thing that can answer the same.
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
