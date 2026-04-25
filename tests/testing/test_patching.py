import inspect

import pytest
from django.conf import settings as django_settings

from next.components.manager import components_manager
from next.deps.providers import ParameterProvider
from next.deps.resolver import resolver
from next.forms.manager import form_action_manager
from next.static.collector import StaticCollector
from next.static.manager import StaticManager, default_manager
from next.testing.patching import (
    StaticCollectorProxy,
    override_component_backends,
    override_dependency,
    override_form_action,
    override_next_settings,
    override_provider,
    patch_static_collector,
)


_BOOM = RuntimeError("boom")


class TestOverrideNextSettings:
    """`override_next_settings` merges into NEXT_FRAMEWORK and restores on exit."""

    def test_merges_without_dropping_untouched_keys(self) -> None:
        with (
            override_next_settings(URL_NAME_TEMPLATE="{module_path}:{name}"),
            override_next_settings(LAZY_COMPONENT_MODULES=True),
        ):
            merged = django_settings.NEXT_FRAMEWORK
            assert merged["URL_NAME_TEMPLATE"] == "{module_path}:{name}"
            assert merged["LAZY_COMPONENT_MODULES"] is True

    def test_restores_on_exit(self) -> None:
        original = getattr(django_settings, "NEXT_FRAMEWORK", None)
        with override_next_settings(URL_NAME_TEMPLATE="{name}"):
            pass
        assert getattr(django_settings, "NEXT_FRAMEWORK", None) == original

    def test_restores_on_exception(self) -> None:
        original = getattr(django_settings, "NEXT_FRAMEWORK", None)
        with pytest.raises(RuntimeError), override_next_settings(URL_NAME_TEMPLATE="x"):
            raise _BOOM
        assert getattr(django_settings, "NEXT_FRAMEWORK", None) == original


class TestOverrideDependency:
    """`override_dependency` binds `Depends(name)` for the block."""

    def test_sets_and_restores(self) -> None:
        resolver._dependency_callables.pop("x_value", None)
        with override_dependency("x_value", 42):
            assert resolver._dependency_callables["x_value"]() == 42
        assert "x_value" not in resolver._dependency_callables

    def test_preserves_existing_binding(self) -> None:
        resolver._dependency_callables["y_value"] = lambda: "orig"
        try:
            with override_dependency("y_value", "temp"):
                assert resolver._dependency_callables["y_value"]() == "temp"
            assert resolver._dependency_callables["y_value"]() == "orig"
        finally:
            resolver._dependency_callables.pop("y_value", None)

    def test_restores_on_exception(self) -> None:
        resolver._dependency_callables.pop("z", None)
        with pytest.raises(RuntimeError), override_dependency("z", 1):
            raise _BOOM
        assert "z" not in resolver._dependency_callables


class _StubProvider(ParameterProvider):
    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        return param.name == "flag"

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        return "STUB"


class TestOverrideProvider:
    """`override_provider` prepends a provider for the block."""

    def test_wins_over_default(self) -> None:
        stub = _StubProvider()
        with override_provider(stub):

            def fn(flag: str = "") -> None:
                return None

            ctx = resolver._providers[0]
            assert ctx is stub
        assert stub not in resolver._providers

    def test_restores_on_exception(self) -> None:
        stub = _StubProvider()
        with pytest.raises(RuntimeError), override_provider(stub):
            raise _BOOM
        assert stub not in resolver._providers


class TestOverrideFormAction:
    """`override_form_action` registers a handler for the block and restores."""

    def test_registers_and_restores(self) -> None:
        backend = form_action_manager.default_backend
        assert "_pt_override" not in backend._registry  # type: ignore[attr-defined]

        def handler() -> str:
            return "ok"

        with override_form_action("_pt_override", handler):
            assert "_pt_override" in backend._registry  # type: ignore[attr-defined]
        assert "_pt_override" not in backend._registry  # type: ignore[attr-defined]

    def test_restores_after_exception(self) -> None:
        backend = form_action_manager.default_backend

        def handler() -> str:
            return ""

        with pytest.raises(RuntimeError), override_form_action("_pt_err", handler):
            raise _BOOM
        assert "_pt_err" not in backend._registry  # type: ignore[attr-defined]


class TestOverrideComponentBackends:
    """`override_component_backends` swaps the backend list for the block."""

    def test_swaps_backends_during_block(self, tmp_path) -> None:
        root = tmp_path / "c"
        root.mkdir()
        config = {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(root)],
            "COMPONENTS_DIR": "_components",
        }
        with override_component_backends(config):
            backends_inside = list(components_manager._backends)
            assert len(backends_inside) == 1
        # After exit, settings_reloaded rebuilds from restored settings.
        components_manager._ensure_backends()


class TestPatchStaticCollector:
    """`patch_static_collector` swaps `create_collector` and restores it."""

    def test_replaces_factory_and_captures_collector(self) -> None:
        _ = default_manager.create_collector
        manager = default_manager._wrapped
        with patch_static_collector(capture=True) as proxy:
            assert isinstance(proxy, StaticCollectorProxy)
            collector = manager.create_collector()
            assert proxy.collector is collector
        assert manager.create_collector.__func__ is StaticManager.create_collector

    def test_custom_factory(self) -> None:
        sentinel = StaticCollector()
        _ = default_manager.create_collector
        manager = default_manager._wrapped
        with patch_static_collector(lambda: sentinel, capture=True) as proxy:
            out = manager.create_collector()
            assert out is sentinel
            assert proxy.collector is sentinel

    def test_no_proxy_when_capture_false(self) -> None:
        with patch_static_collector() as proxy:
            assert proxy is None

    def test_restores_after_exception(self) -> None:
        _ = default_manager.create_collector
        manager = default_manager._wrapped
        with pytest.raises(RuntimeError), patch_static_collector():
            raise _BOOM
        assert manager.create_collector.__func__ is StaticManager.create_collector
