from unittest.mock import patch

from next.forms import RegistryFormActionBackend, form_action_manager
from next.testing import reset_components, reset_form_actions, reset_registries


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
    """reset_components triggers manager._reload_config."""

    def test_calls_reload_on_manager(self) -> None:
        with patch(
            "next.testing.isolation.components_manager._reload_config"
        ) as reload:
            reset_components()
        reload.assert_called_once_with()


class TestResetRegistries:
    """reset_registries calls both helpers."""

    def test_invokes_both(self) -> None:
        with (
            patch("next.testing.isolation.reset_form_actions") as a,
            patch("next.testing.isolation.reset_components") as b,
        ):
            reset_registries()
        a.assert_called_once_with()
        b.assert_called_once_with()


class _BackendWithoutClear:
    def generate_urls(self) -> list:
        return []
