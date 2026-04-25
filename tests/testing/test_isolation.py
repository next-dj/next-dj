from pathlib import Path

from next.components.manager import components_manager
from next.forms import RegistryFormActionBackend, form_action_manager
from next.pages.manager import page
from next.testing import (
    reset_components,
    reset_form_actions,
    reset_page_cache,
    reset_registries,
)


class TestResetFormActions:
    """reset_form_actions clears registries on every backend."""

    def test_clears_registry_and_can_be_repopulated(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action("alpha", lambda: None)
        assert "alpha" in backend._registry
        backend.clear_registry()
        assert backend._registry == {}
        backend.register_action("alpha", lambda: None)
        assert "alpha" in backend._registry

    def test_reset_form_actions_clears_global_manager(self) -> None:
        saved_registry = dict(form_action_manager.default_backend._registry)
        saved_uids = dict(form_action_manager.default_backend._uid_to_name)
        try:
            reset_form_actions()
            assert form_action_manager.default_backend._registry == {}
            assert form_action_manager.default_backend._uid_to_name == {}
        finally:
            form_action_manager.default_backend._registry.update(saved_registry)
            form_action_manager.default_backend._uid_to_name.update(saved_uids)

    def test_clear_registries_skips_backends_without_method(self) -> None:
        manager_backends = form_action_manager._backends
        stub = _BackendWithoutClear()
        form_action_manager._backends = [*manager_backends, stub]
        try:
            saved = dict(form_action_manager.default_backend._registry)
            saved_uids = dict(form_action_manager.default_backend._uid_to_name)
            try:
                form_action_manager.clear_registries()
            finally:
                form_action_manager.default_backend._registry.update(saved)
                form_action_manager.default_backend._uid_to_name.update(saved_uids)
        finally:
            form_action_manager._backends = manager_backends


class TestResetComponents:
    """reset_components rebuilds the component backends list."""

    def test_reset_forces_next_access_to_reload(self) -> None:
        components_manager._ensure_backends()
        original = list(components_manager._backends)
        sentinel = object()
        components_manager._backends.append(sentinel)
        try:
            reset_components()
            components_manager._ensure_backends()
            assert sentinel not in components_manager._backends
        finally:
            components_manager._backends = original


class TestResetRegistries:
    """reset_registries clears form and component state in one call."""

    def test_clears_form_actions_and_reloads_components(self) -> None:
        backend = form_action_manager.default_backend
        saved_registry = dict(backend._registry)
        saved_uids = dict(backend._uid_to_name)
        components_manager._ensure_backends()
        saved_components = list(components_manager._backends)
        sentinel = object()
        components_manager._backends.append(sentinel)
        try:
            reset_registries()
            assert backend._registry == {}
            assert backend._uid_to_name == {}
            components_manager._ensure_backends()
            assert sentinel not in components_manager._backends
        finally:
            backend._registry.update(saved_registry)
            backend._uid_to_name.update(saved_uids)
            components_manager._backends = saved_components


class TestResetPageCache:
    """reset_page_cache drops the template registry and mtime bookkeeping."""

    def test_clears_both_dicts(self) -> None:
        fp = Path("/tmp/synthetic_page.py")
        page._template_registry[fp] = "<p>x</p>"
        page._template_source_mtimes[fp] = {}
        reset_page_cache()
        assert fp not in page._template_registry
        assert fp not in page._template_source_mtimes


class _BackendWithoutClear:
    def generate_urls(self) -> list:
        return []
