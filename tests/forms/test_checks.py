import pytest

from next.forms import RegistryFormActionBackend
from next.forms.base import _invalid_meta_scope_classes, _outside_base_dir_classes
from next.forms.checks import (
    _action_collisions,
    _handler_fingerprint,
    check_action_applied_to_class,
    check_form_action_backends_configuration,
    check_form_action_collisions,
    check_forms_outside_base_dir,
    check_invalid_form_meta_scope,
    record_possible_collision,
)
from next.forms.decorators import _action_applied_to_class, action
from next.forms.signals import action_registered


_FAKE_FILE = "/fake/myapp/forms.py"


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
        backend.register_action(
            "solo",
            handler=lambda: None,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        assert check_form_action_collisions() == []

    def test_duplicate_handlers_trigger_error(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action(
            "dup",
            handler=_distinct_handler("a"),
            file_path=_FAKE_FILE,
            scope="shared",
        )
        backend.register_action(
            "dup",
            handler=_distinct_handler("b"),
            file_path=_FAKE_FILE,
            scope="shared",
        )
        errors = check_form_action_collisions()
        assert len(errors) == 1
        assert errors[0].id == "next.E041"
        assert "dup" in errors[0].msg

    def test_reregistration_of_same_handler_is_safe(self) -> None:
        backend = RegistryFormActionBackend()
        same = _distinct_handler("stable")
        backend.register_action(
            "reload_me",
            handler=same,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        backend.register_action(
            "reload_me",
            handler=same,
            file_path=_FAKE_FILE,
            scope="shared",
        )
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
        assert callable(_handler_fingerprint)
        assert callable(record_possible_collision)

    def test_first_registration_records_no_collision(self) -> None:
        """Common case: a name registered once never touches the collision map."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            "only",
            handler=_distinct_handler("x"),
            file_path=_FAKE_FILE,
            scope="shared",
        )
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

    def test_non_list_setting_is_e044(self, settings) -> None:
        """Top-level value must be a list."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": "x"}
        errors = check_form_action_backends_configuration()
        assert len(errors) == 1
        assert errors[0].id == "next.E044"

    def test_non_dict_entry_is_e044(self, settings) -> None:
        """Each entry must be a dict."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": ["nope"]}
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E044" for e in errors)

    def test_non_string_backend_is_e044(self, settings) -> None:
        """`BACKEND` must be a string."""
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": 7}]}
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E044" for e in errors)

    def test_unimportable_backend_is_e044(self, settings) -> None:
        """A path that fails to import surfaces the original error."""
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_ACTION_BACKENDS": [{"BACKEND": "no.such.Module"}],
        }
        errors = check_form_action_backends_configuration()
        assert any(
            e.id == "next.E044" and "cannot be imported" in e.msg for e in errors
        )

    def test_wrong_type_backend_is_e045(self, settings) -> None:
        """A class that is not a `FormActionBackend` subclass triggers E045."""
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


class TestCheckFormsOutsideBaseDir:
    """check_forms_outside_base_dir: E046 warning when form is declared outside BASE_DIR."""

    def test_no_outside_classes_returns_empty(self) -> None:
        """No warnings when no forms are outside BASE_DIR."""
        _outside_base_dir_classes.clear()
        assert check_forms_outside_base_dir() == []

    def test_outside_class_triggers_warning(self) -> None:
        """A form class outside BASE_DIR produces an E046 warning."""
        _outside_base_dir_classes.clear()
        _outside_base_dir_classes.append(("OutsideForm", "/outside/dir/forms.py"))
        warnings = check_forms_outside_base_dir()
        assert len(warnings) == 1
        assert warnings[0].id == "next.E046"
        assert "OutsideForm" in warnings[0].msg
        _outside_base_dir_classes.clear()

    def test_multiple_outside_classes(self) -> None:
        """Multiple outside-BASE_DIR forms each produce one warning."""
        _outside_base_dir_classes.clear()
        _outside_base_dir_classes.append(("FormA", "/a/forms.py"))
        _outside_base_dir_classes.append(("FormB", "/b/forms.py"))
        warnings = check_forms_outside_base_dir()
        assert len(warnings) == 2
        ids = {w.id for w in warnings}
        assert ids == {"next.E046"}
        _outside_base_dir_classes.clear()


class TestCheckInvalidFormMetaScope:
    """check_invalid_form_meta_scope: E047 error when Meta.scope has invalid value."""

    def test_no_invalid_classes_returns_empty(self) -> None:
        """No errors when all forms have valid Meta.scope."""
        _invalid_meta_scope_classes.clear()
        assert check_invalid_form_meta_scope() == []

    def test_invalid_meta_scope_triggers_error(self) -> None:
        """A form with invalid Meta.scope produces an E047 error."""
        _invalid_meta_scope_classes.clear()
        _invalid_meta_scope_classes.append(("BadScopeForm", "global"))
        errors = check_invalid_form_meta_scope()
        assert len(errors) == 1
        assert errors[0].id == "next.E047"
        assert "BadScopeForm" in errors[0].msg
        assert "global" in errors[0].msg
        _invalid_meta_scope_classes.clear()


class TestCheckActionAppliedToClass:
    """check_action_applied_to_class: E053 error when @action was used on a class."""

    def test_no_class_applications_returns_empty(self) -> None:
        """No errors when @action was never applied to a class."""
        _action_applied_to_class.clear()
        assert check_action_applied_to_class() == []

    def test_class_application_triggers_error(self) -> None:
        """Using @action on a class produces an E053 error."""
        _action_applied_to_class.clear()
        _action_applied_to_class.append("MyBadClass")
        errors = check_action_applied_to_class()
        assert len(errors) == 1
        assert errors[0].id == "next.E053"
        assert "MyBadClass" in errors[0].msg
        _action_applied_to_class.clear()

    def test_applying_action_to_class_raises_and_records(self) -> None:
        """@action raises TypeError on a class and records the qualname in E053 list."""
        _action_applied_to_class.clear()
        with pytest.raises(TypeError, match="form-less actions only"):

            @action("bad_class")
            class BadTargetClass:
                pass

        errors = check_action_applied_to_class()
        assert len(errors) == 1
        assert errors[0].id == "next.E053"
        _action_applied_to_class.clear()
