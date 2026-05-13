import pytest

from next.forms import RegistryFormActionBackend
from next.forms.checks import (
    _action_collisions,
    _handler_fingerprint,
    check_form_action_backends_configuration,
    check_form_action_collisions,
    record_possible_collision,
)
from next.forms.signals import action_registered


@pytest.fixture(autouse=True)
def _reset_collision_cache():
    _action_collisions.clear()
    yield
    _action_collisions.clear()


def _distinct_handler(tag: str):
    def _handler():
        return tag

    _handler.__qualname__ = f"distinct_handler_{tag}"
    return _handler


class TestFormActionCollisions:
    """check_form_action_collisions flags duplicate handlers on one action name."""

    def test_single_registration_is_clean(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action("solo", lambda: None)
        assert check_form_action_collisions() == []

    def test_duplicate_handlers_trigger_error(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action("dup", _distinct_handler("a"))
        backend.register_action("dup", _distinct_handler("b"))
        errors = check_form_action_collisions()
        assert len(errors) == 1
        assert errors[0].id == "next.E041"
        assert "'dup'" in errors[0].msg

    def test_reregistration_of_same_handler_is_safe(self) -> None:
        backend = RegistryFormActionBackend()
        same = _distinct_handler("stable")
        backend.register_action("reload_me", same)
        backend.register_action("reload_me", same)
        assert check_form_action_collisions() == []

    def test_tracker_is_not_signal_receiver(self) -> None:
        """``register_action`` must not pay signal-dispatch cost for the tracker."""
        connected_names = {
            getattr(receiver, "__name__", "")
            for _id, ref in action_registered.receivers
            for receiver in (ref(),)
            if receiver is not None
        }
        assert "record_possible_collision" not in connected_names
        # The helpers must still exist and remain callable from the
        # backend's direct path.
        assert callable(_handler_fingerprint)
        assert callable(record_possible_collision)

    def test_first_registration_records_no_collision(self) -> None:
        """Common case: a name registered once never touches the collision map."""
        backend = RegistryFormActionBackend()
        backend.register_action("only", _distinct_handler("x"))
        assert _action_collisions == {}


class TestFormActionBackendsConfigurationCheck:
    """`check_form_action_backends_configuration` validates dotted paths."""

    def test_no_setting_yields_no_errors(self, settings) -> None:
        """Absent key returns empty list."""
        settings.NEXT_FRAMEWORK = {}
        assert check_form_action_backends_configuration() == []

    def test_settings_not_dict_yields_no_errors(self, settings) -> None:
        """`NEXT_FRAMEWORK` not being a dict short-circuits cleanly."""
        settings.NEXT_FRAMEWORK = "garbage"
        assert check_form_action_backends_configuration() == []

    def test_non_list_setting_is_e042(self, settings) -> None:
        """Top-level value must be a list."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": "x"}
        errors = check_form_action_backends_configuration()
        assert len(errors) == 1
        assert errors[0].id == "next.E044"

    def test_non_dict_entry_is_e042(self, settings) -> None:
        """Each entry must be a dict."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": ["nope"]}
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E044" for e in errors)

    def test_non_string_backend_is_e042(self, settings) -> None:
        """`BACKEND` must be a string."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": 7}]}
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E044" for e in errors)

    def test_unimportable_backend_is_e042(self, settings) -> None:
        """A path that fails to import surfaces the original error."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": "no.such.Module"}],
        }
        errors = check_form_action_backends_configuration()
        assert any(
            e.id == "next.E044" and "cannot be imported" in e.msg for e in errors
        )

    def test_wrong_type_backend_is_e043(self, settings) -> None:
        """A class that is not a `FormActionBackend` subclass triggers E043."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": "django.http.HttpResponse"}],
        }
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E045" for e in errors)

    def test_valid_default_backend_is_clean(self, settings) -> None:
        """Default backend path passes the check without errors."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }
        assert check_form_action_backends_configuration() == []
