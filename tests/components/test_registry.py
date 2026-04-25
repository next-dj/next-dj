from pathlib import Path
from unittest.mock import MagicMock

from next.components import (
    ComponentInfo,
    ComponentRegistry,
    ComponentScanner,
    ComponentVisibilityResolver,
    component_extra_roots_from_config,
    registry as registry_mod,
)


class TestComponentRegistry:
    """ComponentRegistry helpers and dunders."""

    def test_root_mark_clear_iter_contains_len(self, tmp_path: Path) -> None:
        """mark_as_root, is_root, clear, __contains__, __iter__, __len__."""
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
        info2 = ComponentInfo(
            "n",
            broken_root,
            "",
            tmp_path / "n.djx",
            None,
            True,
        )
        assert info2.resolved_scope_root is broken_root

    def test_contains_is_indexed_by_name(self, tmp_path: Path) -> None:
        """Name lookup does not scan every row."""
        reg = ComponentRegistry()
        root = tmp_path.resolve()
        for i in range(50):
            reg.register(
                ComponentInfo(f"c{i}", root, "", tmp_path / f"{i}.djx", None, True),
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
            {"DIRS": [str(b.resolve()), Path(str(a))]},
        )
        assert {Path(p).resolve() for p in r2} == {a.resolve(), b.resolve()}
        missing = tmp_path / "nope"
        assert not missing.exists()
        r3 = component_extra_roots_from_config(
            {"DIRS": [str(a.resolve()), str(missing)]},
        )
        assert r3 == [a.resolve()]

        assert component_extra_roots_from_config({"DIRS": [str(missing)]}) == []


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
            ComponentInfo(
                "c",
                pages.resolve(),
                "about",
                comp_dir / "c.djx",
                None,
                True,
            )
        )
        resolver = ComponentVisibilityResolver(reg)
        outside = tmp_path / "elsewhere" / "t.djx"
        outside.parent.mkdir(parents=True)
        assert resolver.resolve_visible(outside) == {}

    def test_path_cache_and_clear_cache(self, tmp_path: Path) -> None:
        """The second resolve reuses the cache. ``clear_cache`` resets it."""
        pages = tmp_path / "pages"
        tmpl = pages / "home.djx"
        tmpl.parent.mkdir(parents=True)
        tmpl.write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo(
                "c",
                pages.resolve(),
                "",
                pages / "_components" / "c.djx",
                None,
                True,
            )
        )
        (pages / "_components").mkdir()
        (pages / "_components" / "c.djx").write_text("y")
        res = ComponentVisibilityResolver(reg)
        r1 = res.resolve_visible(tmpl)
        r2 = res.resolve_visible(tmpl)
        assert r1 == r2
        assert "c" in r1
        assert r1["c"].name == "c"
        res.clear_cache()
        assert res._path_cache == {}

    def test_scope_index_reused_for_second_template_path(self, tmp_path: Path) -> None:
        """Second template path does not rebuild the per-root index."""
        pages = tmp_path / "pages"
        comp_dir = pages / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "c.djx").write_text("x")
        reg = ComponentRegistry()
        reg.register(
            ComponentInfo(
                "c",
                pages.resolve(),
                "about",
                comp_dir / "c.djx",
                None,
                True,
            )
        )
        res = ComponentVisibilityResolver(reg)
        t1 = pages / "about" / "a.djx"
        t2 = pages / "about" / "b.djx"
        t1.parent.mkdir(parents=True, exist_ok=True)
        t1.write_text("z")
        t2.write_text("z")
        res.resolve_visible(t1)
        res.resolve_visible(t2)

    def test_global_root_component_with_scope_relative_not_visible_far_away(
        self, tmp_path: Path
    ) -> None:
        """Marked global root still checks scope path when ``scope_relative`` is set."""
        root = tmp_path / "global"
        root.mkdir()
        reg = ComponentRegistry()
        reg.mark_as_root(root.resolve())
        reg.register(
            ComponentInfo(
                "x",
                root.resolve(),
                "onlyhere",
                root / "x.djx",
                None,
                True,
            )
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
            res._compute_relative_parts(
                tmp_path / "a" / "t.djx",
                tmp_path / "b",
            )
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
        monkeypatch.setattr(registry_mod, "_VISIBILITY_CACHE_MAX_SIZE", 2)
        reg = ComponentRegistry()
        scope_root = (tmp_path / "scope").resolve()
        scope_root.mkdir()
        sub = scope_root / "area"
        sub.mkdir()
        # Two components share the same scope_root so a single `resolve_visible()`
        # call queries `_get_relative_parts_cached` twice for that key: the second
        # lookup hits the cache and exercises `move_to_end`.
        reg.register(
            ComponentInfo("c1", scope_root, "area", sub / "c1.djx", None, True),
        )
        reg.register(
            ComponentInfo("c2", scope_root, "area", sub / "c2.djx", None, True),
        )

        res = ComponentVisibilityResolver(reg)
        paths = [sub / f"t{i}.djx" for i in range(3)]
        for p in paths:
            p.write_text("x")
            res.resolve_visible(p)

        assert paths[0].resolve() not in res._result_cache
        assert (paths[0].resolve(), scope_root) not in res._path_cache
        assert paths[-1].resolve() in res._result_cache
