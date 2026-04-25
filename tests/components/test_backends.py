import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import next.components as next_components_mod
from next.components import (
    ComponentInfo,
    ComponentsFactory,
    ComponentsManager,
    DummyBackend,
    FileComponentsBackend,
    ModuleCache,
    ModuleLoader,
    component_extra_roots_from_config,
)
from tests.support import (
    next_framework_settings_component_backends_list as _next_framework_settings_component_backends_list,
)


class TestComponentsModuleExports:
    """``next.components`` public API surface."""

    def test_all_names_exist_on_module(self) -> None:
        """Every name in ``__all__`` exists on the module."""
        for name in next_components_mod.__all__:
            assert hasattr(next_components_mod, name)


class TestComponentInfo:
    """Tests for ComponentInfo dataclass."""

    def test_component_info_simple(self) -> None:
        """Simple component has template_path and no module_path."""
        info = ComponentInfo(
            name="card",
            scope_root=Path("/app/pages"),
            scope_relative="",
            template_path=Path("/app/pages/_components/card.djx"),
            module_path=None,
            is_simple=True,
        )
        assert info.name == "card"
        assert info.is_simple
        assert info.template_path is not None
        assert info.module_path is None


class TestComponentInfoDunders:
    """ComponentInfo repr, hash, eq, scope_key."""

    def test_repr_contains_fields(self) -> None:
        """Repr includes name and scope fields."""
        root = Path("/app/pages")
        info = ComponentInfo(
            name="card",
            scope_root=root,
            scope_relative="blog",
            template_path=root / "card.djx",
            module_path=None,
            is_simple=True,
        )
        r = repr(info)
        assert "card" in r
        assert "blog" in r
        assert "ComponentInfo" in r

    def test_hash_eq_includes_paths(self) -> None:
        """Same name and scope but different files are not equal. ``scope_key`` can still match."""
        r = Path("/p")
        a = ComponentInfo("x", r, "", Path("/p/a.djx"), None, True)
        b = ComponentInfo("x", r, "", Path("/p/b.djx"), None, True)
        c = ComponentInfo("x", r, "sub", Path("/p/a.djx"), None, True)
        assert a != b
        assert a.scope_key == b.scope_key
        assert a != c
        d = ComponentInfo("x", r, "", Path("/p/a.djx"), None, True)
        assert a == d
        assert hash(a) == hash(d)
        assert a != object()


class TestFileComponentsBackend:
    """Tests for FileComponentsBackend discovery and resolution."""

    def test_collect_visible_empty_when_no_roots(
        self, min_component_config: dict
    ) -> None:
        """With empty ``DIRS`` and no registry data, no components are visible."""
        backend = FileComponentsBackend(dict(min_component_config))
        visible = backend.collect_visible_components(Path("/tmp/some/template.djx"))
        assert visible == {}

    def test_get_component_returns_none_when_empty(
        self, min_component_config: dict
    ) -> None:
        """get_component returns None when no backends have it."""
        backend = FileComponentsBackend(dict(min_component_config))
        assert backend.get_component("card", Path("/tmp/template.djx")) is None

    def test_discover_in_component_root_simple(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Root component dir: .djx files are discovered as simple components."""
        (tmp_path / "header.djx").write_text("<header>Hi</header>")
        backend = FileComponentsBackend(
            {**min_component_config, "DIRS": [str(tmp_path)]},
        )
        backend._ensure_loaded()
        assert len(backend._registry) == 1
        components = list(backend._registry)
        assert len(components) == 1
        info = components[0]
        assert info.name == "header"
        assert info.scope_relative == ""
        assert info.is_simple
        assert info.template_path == tmp_path / "header.djx"

    def test_discover_in_component_root_composite(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Root component dir: subdir with component.djx is composite."""
        (tmp_path / "profile").mkdir()
        (tmp_path / "profile" / "component.djx").write_text("<div>profile</div>")
        backend = FileComponentsBackend(
            {**min_component_config, "DIRS": [str(tmp_path)]},
        )
        backend._ensure_loaded()
        assert len(backend._registry) == 1
        components = list(backend._registry)
        info = components[0]
        assert info.name == "profile"
        assert not info.is_simple
        assert info.template_path == tmp_path / "profile" / "component.djx"

    def test_string_base_dir_normalized_for_discovery(self, tmp_path: Path) -> None:
        """``BASE_DIR`` as str is converted to ``Path`` for ``DIRS`` resolution."""
        (tmp_path / "nest").mkdir()
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = str(tmp_path)
            roots = component_extra_roots_from_config({"DIRS": ["nest"]})
        assert roots == [(tmp_path / "nest").resolve()]

    def test_file_components_backend_normalizes_string_base_dir(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """``BASE_DIR`` as str is normalized when resolving ``DIRS``."""
        (tmp_path / "c").mkdir()
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = str(tmp_path)
            FileComponentsBackend({**min_component_config, "DIRS": ["c"]})

    def test_discover_component_roots_from_dirs(self, tmp_path: Path) -> None:
        """``component_extra_roots_from_config`` returns existing paths from ``DIRS``."""
        assert component_extra_roots_from_config({"DIRS": ["/nonexistent/root"]}) == []

        roots = component_extra_roots_from_config({"DIRS": [str(tmp_path)]})
        assert len(roots) == 1
        assert roots[0] == tmp_path.resolve()

    def test_root_components_visible_from_any_path(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Root component roots are visible from any template path."""
        (tmp_path / "global.djx").write_text("<div>global</div>")
        backend = FileComponentsBackend(dict(min_component_config))

        info = ComponentInfo(
            name="global",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "global.djx",
            module_path=None,
            is_simple=True,
        )
        backend._registry.register(info)
        backend._registry.mark_as_root(tmp_path)
        backend._loaded = True

        visible = backend.collect_visible_components(Path("/other/path/template.djx"))
        assert "global" in visible

    def test_visible_from_template_under_scope(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Component in scope_relative is visible from template under that path."""
        comp_dir = tmp_path / "pages" / "about" / "_components"
        comp_dir.mkdir(parents=True)
        (comp_dir / "card.djx").write_text("<div>card</div>")
        backend = FileComponentsBackend(dict(min_component_config))

        info = ComponentInfo(
            name="card",
            scope_root=tmp_path / "pages",
            scope_relative="about",
            template_path=comp_dir / "card.djx",
            module_path=None,
            is_simple=True,
        )
        backend._registry.register(info)
        backend._loaded = True

        template_path = tmp_path / "pages" / "about" / "team" / "template.djx"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        visible = backend.collect_visible_components(template_path)
        assert "card" in visible
        assert visible["card"].name == "card"


class TestComponentsFactory:
    """Tests for ComponentsFactory."""

    def test_create_backend_file_default(self) -> None:
        """Create FileComponentsBackend with merged-style keys."""
        config = {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "_components",
        }
        backend = ComponentsFactory.create_backend(config)
        assert isinstance(backend, FileComponentsBackend)
        assert backend.components_dir == "_components"

    def test_create_backend_file_with_component_dirs(self) -> None:
        """Create FileComponentsBackend with ``COMPONENTS_DIR`` and empty ``DIRS``."""
        config = {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [],
            "COMPONENTS_DIR": "components",
        }
        backend = ComponentsFactory.create_backend(config)
        assert isinstance(backend, FileComponentsBackend)
        assert backend.components_dir == "components"
        assert backend._extra_component_roots == []

    def test_create_backend_unknown_raises(self) -> None:
        """Unknown backend class path raises ImportError."""
        with pytest.raises(ImportError):
            ComponentsFactory.create_backend(
                {"BACKEND": "next.components.UnknownBackend"}
            )


class TestComponentsFactoryManager:
    """ComponentsFactory import path and ComponentsManager branches."""

    def test_create_backend_imports_class_and_passes_config(self) -> None:
        """Backend is loaded by dotted path and receives the full config dict."""
        b = ComponentsFactory.create_backend(
            {
                "BACKEND": "next.components.DummyBackend",
                "OPTIONS": {"marker": 7},
            },
        )
        assert isinstance(b, DummyBackend)
        assert b.config["OPTIONS"]["marker"] == 7

    def test_dummy_backend_lookups_are_empty(self) -> None:
        """DummyBackend does not resolve names and reports no visible components."""
        b = DummyBackend({})
        assert b.get_component("x", Path("/t.djx")) is None
        assert b.collect_visible_components(Path("/t.djx")) == {}

    def test_manager_skips_non_list_config_and_non_dict_entries(self) -> None:
        """If ``DEFAULT_COMPONENT_BACKENDS`` is not a list, return early. Non-dict entries are skipped."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list("bad")
        with patch("next.components.manager.next_framework_settings", mock_ns):
            mgr._reload_config()
            assert mgr._backends == []

        mgr2 = ComponentsManager()
        mock_ns2 = _next_framework_settings_component_backends_list(
            [
                None,
                {
                    "BACKEND": "next.components.FileComponentsBackend",
                    "DIRS": [],
                    "COMPONENTS_DIR": "_components",
                },
            ],
        )
        with patch("next.components.manager.next_framework_settings", mock_ns2):
            mgr2._reload_config()
            assert len(mgr2._backends) >= 1

    def test_manager_swallows_backend_init_exception(self) -> None:
        """An exception from ``create_backend`` is logged. The backend is not appended."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [
                {
                    "BACKEND": "next.components.BoomBackend",
                    "DIRS": [],
                    "COMPONENTS_DIR": "_components",
                },
            ],
        )
        with patch("next.components.manager.next_framework_settings", mock_ns):
            mgr._reload_config()
        assert mgr._backends == []

    def test_manager_collect_visible_first_backend_wins(self) -> None:
        """Same component name from two backends: first backend wins."""
        mgr = ComponentsManager()
        info1 = ComponentInfo("a", Path("/"), "", None, None, True)
        info2 = ComponentInfo("a", Path("/b"), "", None, None, True)
        b1 = MagicMock()
        b1.collect_visible_components.return_value = {"a": info1}
        b2 = MagicMock()
        b2.collect_visible_components.return_value = {"a": info2}
        mgr._backends = [b1, b2]
        merged = mgr.collect_visible_components(Path("/t.djx"))
        assert merged["a"] is info1

    def test_manager_get_component_none_from_all_backends(self) -> None:
        """get_component returns None when every backend returns None."""
        mgr = ComponentsManager()
        b = MagicMock()
        b.get_component.return_value = None
        mgr._backends = [b]
        assert mgr.get_component("x", Path("/p")) is None

    def test_manager_get_component_returns_first_hit(self) -> None:
        """get_component returns first non-None from backends."""
        mgr = ComponentsManager()
        hit = ComponentInfo("n", Path("/"), "", None, None, True)
        b1 = MagicMock()
        b1.get_component.return_value = None
        b2 = MagicMock()
        b2.get_component.return_value = hit
        mgr._backends = [b1, b2]
        assert mgr.get_component("n", Path("/t")) is hit


class TestModuleCache:
    """ModuleCache LRU and dunder methods."""

    def test_lru_evicts_oldest_when_at_capacity(self, tmp_path: Path) -> None:
        """Adding a new path when full removes the least recently used entry."""
        cache = ModuleCache(maxsize=2)
        p1 = tmp_path / "a.py"
        p2 = tmp_path / "b.py"
        p3 = tmp_path / "c.py"
        m1 = types.ModuleType("a")
        m2 = types.ModuleType("b")
        m3 = types.ModuleType("c")
        cache.set(p1, m1)
        cache.set(p2, m2)
        cache.get(p1)
        cache.set(p3, m3)
        assert p1 in cache
        assert p3 in cache
        assert p2 not in cache

    def test_len_and_contains(self, tmp_path: Path) -> None:
        """__len__ and __contain__ reflect cache keys."""
        cache = ModuleCache()
        p = tmp_path / "x.py"
        assert len(cache) == 0
        assert p not in cache
        cache.set(p, types.ModuleType("x"))
        assert len(cache) == 1
        assert p in cache

    def test_clear_empties_cache(self, tmp_path: Path) -> None:
        """Clear removes all entries and access order."""
        cache = ModuleCache()
        cache.set(tmp_path / "a.py", types.ModuleType("a"))
        cache.clear()
        assert len(cache) == 0


class TestModuleLoader:
    """ModuleLoader disk paths and cache."""

    def test_load_uses_cache_on_second_call(self, tmp_path: Path) -> None:
        """Second load for the same path does not re-read disk (cache hit updates LRU)."""
        path = tmp_path / "mod.py"
        path.write_text("x = 1\n")
        cache = ModuleCache()
        loader = ModuleLoader(cache)
        m1 = loader.load(path)
        m2 = loader.load(path)
        assert m1 is m2

    def test_load_returns_none_when_spec_missing(self, tmp_path: Path) -> None:
        """_load_from_disk returns None when spec_from_file_location returns None."""
        path = tmp_path / "empty.py"
        path.write_text("pass\n")
        with patch(
            "next.components.loading.importlib.util.spec_from_file_location",
            return_value=None,
        ):
            loader = ModuleLoader(ModuleCache())
            assert loader.load(path) is None

    def test_load_returns_none_when_spec_has_no_loader(self, tmp_path: Path) -> None:
        """_load_from_disk returns None when spec.loader is missing."""
        path = tmp_path / "m.py"
        path.write_text("pass\n")
        spec = types.SimpleNamespace(loader=None)
        with patch(
            "next.components.loading.importlib.util.spec_from_file_location",
            return_value=spec,
        ):
            assert ModuleLoader(ModuleCache()).load(path) is None


class TestModuleLoaderDisk:
    """ModuleLoader loads from disk the same way the old helper did."""

    def test_success_and_failure(self, tmp_path: Path) -> None:
        """A valid module loads. Syntax errors yield ``None``."""
        good = tmp_path / "ok.py"
        good.write_text("ANSWER = 42\n")
        loader = ModuleLoader()
        mod = loader.load(good)
        assert mod is not None
        assert mod.ANSWER == 42

        bad = tmp_path / "bad.py"
        bad.write_text("def x(\n")
        assert loader.load(bad) is None

    def test_no_spec_returns_none(self, tmp_path: Path) -> None:
        """Missing import spec yields ``None``."""
        p = tmp_path / "x.py"
        p.write_text("pass\n")
        with patch(
            "next.components.loading.importlib.util.spec_from_file_location",
            return_value=None,
        ):
            assert ModuleLoader(ModuleCache()).load(p) is None
