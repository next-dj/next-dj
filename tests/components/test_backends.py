import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import override_settings

import next.components as next_components_mod
from next.components import (
    ComponentInfo,
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


def _install(manager: ComponentsManager, *backends: object) -> None:
    """Put ready-made backends on a manager as a finished load."""
    manager._backends = list(backends)
    manager._loaded = True


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
    """ComponentInfo repr, hash, eq."""

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
        """Same name and scope but different files are not equal."""
        r = Path("/p")
        a = ComponentInfo("x", r, "", Path("/p/a.djx"), None, True)
        b = ComponentInfo("x", r, "", Path("/p/b.djx"), None, True)
        c = ComponentInfo("x", r, "sub", Path("/p/a.djx"), None, True)
        assert a != b
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
        """A bare ``.djx`` file in a component root registers as a simple component."""
        (tmp_path / "header.djx").write_text("<header>Hi</header>")
        backend = FileComponentsBackend(
            {**min_component_config, "DIRS": [str(tmp_path)]}
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
        """A subdirectory holding ``component.djx`` registers as a composite."""
        (tmp_path / "profile").mkdir()
        (tmp_path / "profile" / "component.djx").write_text("<div>profile</div>")
        backend = FileComponentsBackend(
            {**min_component_config, "DIRS": [str(tmp_path)]}
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

    def test_import_all_does_not_double_exec_module(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """``import_component_modules`` does not exec ``component.py`` twice."""
        comp_dir = tmp_path / "card"
        comp_dir.mkdir()
        component_py = comp_dir / "component.py"
        component_py.write_text("component = 'card'\n")

        disk_reads: list[Path] = []
        original_load_from_disk = ModuleLoader._load_from_disk

        def tracking_load_from_disk(self, path):
            disk_reads.append(path)
            return original_load_from_disk(self, path)

        with patch.object(ModuleLoader, "_load_from_disk", tracking_load_from_disk):
            backend = FileComponentsBackend(
                {**min_component_config, "DIRS": [str(tmp_path)]}
            )
            backend._ensure_loaded()
            backend.import_component_modules()

        reads_for_comp = [p for p in disk_reads if p == component_py]
        assert len(reads_for_comp) == 1

    def test_import_component_modules_imports_lazy_modules(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Lazy discovery still imports every ``component.py`` on demand."""
        comp_dir = tmp_path / "lazy_c"
        comp_dir.mkdir()
        (comp_dir / "component.py").write_text("# lazy\n")
        (comp_dir / "component.djx").write_text("<div/>")
        config = {**min_component_config, "DIRS": [str(tmp_path)]}

        with override_settings(
            NEXT_FRAMEWORK={
                "COMPONENT_BACKENDS": [config],
                "LAZY_COMPONENT_MODULES": True,
            }
        ):
            backend = FileComponentsBackend(config)
            with patch.object(
                backend._module_loader, "load", wraps=backend._module_loader.load
            ) as load_spy:
                paths = backend.import_component_modules()

        assert paths == (comp_dir / "component.py",)
        assert load_spy.call_count == 1

    def test_discover_stays_lazy_while_the_import_hook_executes(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """``discover`` registers while the import hook stays unrun."""
        comp_dir = tmp_path / "split_c"
        comp_dir.mkdir()
        marker = tmp_path / "imported.txt"
        (comp_dir / "component.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
        )
        (comp_dir / "component.djx").write_text("<div/>")
        config = {**min_component_config, "DIRS": [str(tmp_path)]}

        with override_settings(
            NEXT_FRAMEWORK={
                "COMPONENT_BACKENDS": [config],
                "LAZY_COMPONENT_MODULES": True,
            }
        ):
            backend = FileComponentsBackend(config)
            backend.discover()
            visible = backend.collect_visible_components(tmp_path / "page.djx")
            assert "split_c" in visible
            assert not marker.exists()

            assert backend.import_component_modules() == (comp_dir / "component.py",)
            assert marker.read_text() == "loaded"

    def test_import_component_modules_ignores_module_less_components(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A simple component carries no ``component.py`` and yields no path."""
        (tmp_path / "header.djx").write_text("<header/>")
        backend = FileComponentsBackend(
            {**min_component_config, "DIRS": [str(tmp_path)]}
        )
        assert backend.import_component_modules() == ()

    def test_a_backend_without_modules_inherits_the_no_op_import_hook(self) -> None:
        """The contract default reports no modules rather than failing."""
        assert DummyBackend({}).import_component_modules() == ()


class TestWalkedFolderHook:
    """`register_walked_folder` is how a backend claims a page-tree folder."""

    def test_a_backend_off_the_filesystem_declines_the_folder(
        self, tmp_path: Path
    ) -> None:
        """The contract default answers False so the walk moves on."""
        assert DummyBackend({}).register_walked_folder(tmp_path, tmp_path, "") is False

    def test_the_file_backend_claims_and_registers(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A claim registers the folder's components under the route trail."""
        folder = tmp_path / "blog" / "_components"
        folder.mkdir(parents=True)
        (folder / "card.djx").write_text("<div/>")
        backend = FileComponentsBackend({**min_component_config, "DIRS": []})

        assert backend.register_walked_folder(folder, tmp_path, "blog") is True

        infos = list(backend.iter_components())
        assert [info.name for info in infos] == ["card"]
        assert infos[0].scope_root == tmp_path
        assert infos[0].scope_relative == "blog"

    def test_a_claim_executes_the_component_module(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A composite component's ``component.py`` runs as it is registered."""
        folder = tmp_path / "_components"
        comp_dir = folder / "panel"
        comp_dir.mkdir(parents=True)
        (comp_dir / "component.djx").write_text("<div/>")
        marker = tmp_path / "ran.txt"
        (comp_dir / "component.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n"
        )
        backend = FileComponentsBackend({**min_component_config, "DIRS": []})

        assert backend.register_walked_folder(folder, tmp_path, "") is True
        assert marker.read_text() == "yes"


class TestEnumerationHooks:
    """`iter_components` and `global_component_roots` feed the system checks."""

    def test_a_backend_without_a_list_enumerates_nothing(self) -> None:
        """The contract default keeps an on-demand backend out of the checks."""
        backend = DummyBackend({})
        assert list(backend.iter_components()) == []
        assert list(backend.global_component_roots()) == []

    def test_the_file_backend_enumerates_after_scanning_itself(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Enumerating triggers the same lazy discovery a render would."""
        (tmp_path / "card.djx").write_text("<div/>")
        backend = FileComponentsBackend(
            {**min_component_config, "DIRS": [str(tmp_path)]}
        )
        assert backend._loaded is False

        assert [info.name for info in backend.iter_components()] == ["card"]
        assert backend._loaded is True

    def test_a_dirs_root_is_global_and_a_page_tree_is_not(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Only a configured root resolves its root-scope components everywhere."""
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "card.djx").write_text("<div/>")
        tree = tmp_path / "pages"
        folder = tree / "_components"
        folder.mkdir(parents=True)
        (folder / "panel.djx").write_text("<div/>")
        backend = FileComponentsBackend({**min_component_config, "DIRS": [str(shared)]})
        backend.register_walked_folder(folder, tree, "")

        roots = frozenset(backend.global_component_roots())
        assert roots == frozenset({shared})
        assert tree not in roots


class TestFileBackendFromConfig:
    """A merged `COMPONENT_BACKENDS` entry configures the file backend."""

    def test_empty_dirs_yield_no_extra_roots(self) -> None:
        """Nothing to scan beyond the page trees when ``DIRS`` is empty."""
        backend = FileComponentsBackend(
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_components",
            }
        )
        assert backend._extra_component_roots == []

    def test_the_components_dir_name_is_not_a_root(self) -> None:
        """``COMPONENTS_DIR`` names a router skip folder and never reaches the backend."""
        backend = FileComponentsBackend(
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [str(Path(__file__).parent)],
                "COMPONENTS_DIR": "components",
            }
        )
        assert backend._extra_component_roots == [Path(__file__).parent]


class TestComponentsManagerLoading:
    """`ComponentsManager` branches around the shared backend loader."""

    def test_entry_without_backend_falls_back_to_the_file_backend(self) -> None:
        """An entry naming no ``BACKEND`` still gets the filesystem source."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"DIRS": [], "COMPONENTS_DIR": "_components"}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            mgr.reload()
        assert [type(backend) for backend in mgr._backends] == [FileComponentsBackend]

    def test_backend_receives_its_own_config_entry(self) -> None:
        """The whole entry is handed to the backend constructor."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "next.components.DummyBackend", "OPTIONS": {"marker": 7}}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            mgr.reload()
        backend = mgr._backends[0]
        assert isinstance(backend, DummyBackend)
        assert backend.config["OPTIONS"]["marker"] == 7

    def test_entry_outside_the_family_is_skipped(self) -> None:
        """A class that is no ``ComponentsBackend`` never joins the list."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list(
            [{"BACKEND": "builtins.dict"}]
        )
        with patch("next.backends.next_framework_settings", mock_ns):
            mgr.reload()
        assert mgr._backends == []

    def test_dummy_backend_lookups_are_empty(self) -> None:
        """DummyBackend does not resolve names and reports no visible components."""
        b = DummyBackend({})
        assert b.get_component("x", Path("/t.djx")) is None
        assert b.collect_visible_components(Path("/t.djx")) == {}

    def test_a_backend_without_eager_discovery_answers_the_hook(self) -> None:
        """`discover` is a no-op a backend resolving on demand can inherit."""
        assert DummyBackend({}).discover() is None

    def test_manager_skips_non_list_config_and_non_dict_entries(self) -> None:
        """If ``COMPONENT_BACKENDS`` is not a list, return early. Non-dict entries are skipped."""
        mgr = ComponentsManager()
        mock_ns = _next_framework_settings_component_backends_list("bad")
        with patch("next.backends.next_framework_settings", mock_ns):
            mgr.reload()
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
            ]
        )
        with patch("next.backends.next_framework_settings", mock_ns2):
            mgr2.reload()
            assert len(mgr2._backends) >= 1

    def test_manager_collect_visible_first_backend_wins(self) -> None:
        """The first backend wins a component name two backends both claim."""
        mgr = ComponentsManager()
        info1 = ComponentInfo("a", Path("/"), "", None, None, True)
        info2 = ComponentInfo("a", Path("/b"), "", None, None, True)
        b1 = MagicMock()
        b1.collect_visible_components.return_value = {"a": info1}
        b2 = MagicMock()
        b2.collect_visible_components.return_value = {"a": info2}
        _install(mgr, b1, b2)
        merged = mgr.collect_visible_components(Path("/t.djx"))
        assert merged["a"] is info1

    def test_manager_get_component_none_from_all_backends(self) -> None:
        """get_component returns None when every backend returns None."""
        mgr = ComponentsManager()
        b = MagicMock()
        b.get_component.return_value = None
        _install(mgr, b)
        assert mgr.get_component("x", Path("/p")) is None

    def test_manager_get_component_returns_first_hit(self) -> None:
        """get_component returns first non-None from backends."""
        mgr = ComponentsManager()
        hit = ComponentInfo("n", Path("/"), "", None, None, True)
        b1 = MagicMock()
        b1.get_component.return_value = None
        b2 = MagicMock()
        b2.get_component.return_value = hit
        _install(mgr, b1, b2)
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

    def test_empty_shared_cache_is_kept(self, tmp_path: Path) -> None:
        """An empty ``ModuleCache`` handed in stays the loader's cache.

        ``ModuleCache`` defines ``__len__``, so a fresh one is falsy and a
        truthiness check would silently unshare it from other loaders.
        """
        path = tmp_path / "mod.py"
        path.write_text("x = 1\n")
        shared = ModuleCache()
        assert len(shared) == 0
        loaded = ModuleLoader(shared).load(path)
        assert loaded is not None
        assert path in shared
        assert ModuleLoader(shared).load(path) is loaded

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
