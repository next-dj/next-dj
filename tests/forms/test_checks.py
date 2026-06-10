import pytest
from django.test import override_settings

from next.forms import ActionRegistration, RegistryFormActionBackend
from next.forms.backends import _handler_fingerprint, record_possible_collision
from next.forms.checks import (
    check_action_applied_to_class,
    check_form_action_backends_configuration,
    check_form_action_collisions,
    check_form_anchor_files,
    check_form_wizard_backend,
    check_form_wizard_sessions,
    check_form_wizard_steps,
    check_forms_outside_base_dir,
    check_invalid_form_meta_scope,
)
from next.forms.decorators import action
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics
from next.forms.signals import action_registered


_FAKE_FILE = "/fake/myapp/forms.py"


@pytest.fixture(autouse=True)
def _reset_collision_cache():
    registration_diagnostics.action_collisions.clear()
    yield
    registration_diagnostics.action_collisions.clear()


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
            ActionRegistration(
                name="solo",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        assert check_form_action_collisions() == []

    def test_duplicate_handlers_trigger_error(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="dup",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=_distinct_handler("a"),
            )
        )
        backend.register_action(
            ActionRegistration(
                name="dup",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=_distinct_handler("b"),
            )
        )
        errors = check_form_action_collisions()
        assert len(errors) == 1
        assert errors[0].id == "next.E041"
        assert "dup" in errors[0].msg

    def test_reregistration_of_same_handler_is_safe(self) -> None:
        backend = RegistryFormActionBackend()
        same = _distinct_handler("stable")
        backend.register_action(
            ActionRegistration(
                name="reload_me",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=same,
            )
        )
        backend.register_action(
            ActionRegistration(
                name="reload_me",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=same,
            )
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
            ActionRegistration(
                name="only",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=_distinct_handler("x"),
            )
        )
        assert registration_diagnostics.action_collisions == {}


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
    """check_forms_outside_base_dir: W046 warning when form is declared outside BASE_DIR."""

    def test_no_outside_classes_returns_empty(self) -> None:
        """No warnings when no forms are outside BASE_DIR."""
        registration_diagnostics.outside_base_dir.clear()
        assert check_forms_outside_base_dir() == []

    def test_outside_class_triggers_warning(self) -> None:
        """A form class outside BASE_DIR produces a W046 warning."""
        registration_diagnostics.outside_base_dir.clear()
        registration_diagnostics.outside_base_dir.append(
            ("OutsideForm", "/outside/dir/forms.py")
        )
        warnings = check_forms_outside_base_dir()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W046"
        assert "OutsideForm" in warnings[0].msg
        registration_diagnostics.outside_base_dir.clear()

    def test_multiple_outside_classes(self) -> None:
        """Multiple outside-BASE_DIR forms each produce one warning."""
        registration_diagnostics.outside_base_dir.clear()
        registration_diagnostics.outside_base_dir.append(("FormA", "/a/forms.py"))
        registration_diagnostics.outside_base_dir.append(("FormB", "/b/forms.py"))
        warnings = check_forms_outside_base_dir()
        assert len(warnings) == 2
        ids = {w.id for w in warnings}
        assert ids == {"next.W046"}
        registration_diagnostics.outside_base_dir.clear()


class TestCheckInvalidFormMetaScope:
    """check_invalid_form_meta_scope: E047 error when Meta.scope has invalid value."""

    def test_no_invalid_classes_returns_empty(self) -> None:
        """No errors when all forms have valid Meta.scope."""
        registration_diagnostics.invalid_meta_scope.clear()
        assert check_invalid_form_meta_scope() == []

    def test_invalid_meta_scope_triggers_error(self) -> None:
        """A form with invalid Meta.scope produces an E047 error."""
        registration_diagnostics.invalid_meta_scope.clear()
        registration_diagnostics.invalid_meta_scope.append(("BadScopeForm", "global"))
        errors = check_invalid_form_meta_scope()
        assert len(errors) == 1
        assert errors[0].id == "next.E047"
        assert "BadScopeForm" in errors[0].msg
        assert "global" in errors[0].msg
        registration_diagnostics.invalid_meta_scope.clear()


class TestCheckActionAppliedToClass:
    """check_action_applied_to_class: E053 error when @action was used on a class."""

    def test_no_class_applications_returns_empty(self) -> None:
        """No errors when @action was never applied to a class."""
        registration_diagnostics.action_applied_to_class.clear()
        assert check_action_applied_to_class() == []

    def test_class_application_triggers_error(self) -> None:
        """Using @action on a class produces an E053 error."""
        registration_diagnostics.action_applied_to_class.clear()
        registration_diagnostics.action_applied_to_class.append("MyBadClass")
        errors = check_action_applied_to_class()
        assert len(errors) == 1
        assert errors[0].id == "next.E053"
        assert "MyBadClass" in errors[0].msg
        registration_diagnostics.action_applied_to_class.clear()

    def test_applying_action_to_class_raises_and_records(self) -> None:
        """@action raises TypeError on a class and records the qualname in E053 list."""
        registration_diagnostics.action_applied_to_class.clear()
        with pytest.raises(TypeError, match="form-less actions only"):

            @action("bad_class")
            class BadTargetClass:
                pass

        errors = check_action_applied_to_class()
        assert len(errors) == 1
        assert errors[0].id == "next.E053"
        registration_diagnostics.action_applied_to_class.clear()


class TestCheckFormWizardSteps:
    """check_form_wizard_steps: E050 when a wizard declares no steps."""

    def test_no_stepless_wizards_returns_empty(self) -> None:
        """No errors when every wizard declares steps."""
        registration_diagnostics.wizard_without_steps.clear()
        assert check_form_wizard_steps() == []

    def test_stepless_wizard_triggers_error(self) -> None:
        """A wizard with empty Meta.steps produces an E050 error."""
        registration_diagnostics.wizard_without_steps.clear()
        registration_diagnostics.wizard_without_steps.append("EmptyWizard")
        errors = check_form_wizard_steps()
        assert len(errors) == 1
        assert errors[0].id == "next.E050"
        assert "EmptyWizard" in errors[0].msg
        registration_diagnostics.wizard_without_steps.clear()


class TestCheckFormWizardBackend:
    """check_form_wizard_backend: E051 for a malformed DEFAULT_FORM_WIZARD_BACKEND."""

    def test_no_setting_yields_no_errors(self, settings) -> None:
        """An absent key returns an empty list."""
        settings.NEXT_FRAMEWORK = {}
        assert check_form_wizard_backend() == []

    def test_settings_not_dict_yields_no_errors(self, settings) -> None:
        """`NEXT_FRAMEWORK` not being a dict short-circuits cleanly."""
        settings.NEXT_FRAMEWORK = "garbage"
        assert check_form_wizard_backend() == []

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_FORM_WIZARD_BACKEND": {
                "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
                "OPTIONS": {},
            }
        }
    )
    def test_default_config_is_clean(self) -> None:
        """The default cache backend config passes the check."""
        assert check_form_wizard_backend() == []

    @override_settings(
        NEXT_FRAMEWORK={"DEFAULT_FORM_WIZARD_BACKEND": ["not", "a", "dict"]}
    )
    def test_non_dict_config_is_e051(self) -> None:
        """A non-dict config triggers E051."""
        errors = check_form_wizard_backend()
        assert len(errors) == 1
        assert errors[0].id == "next.E051"

    @override_settings(NEXT_FRAMEWORK={"DEFAULT_FORM_WIZARD_BACKEND": {"BACKEND": 7}})
    def test_non_string_backend_is_e051(self) -> None:
        """`BACKEND` must be a string."""
        errors = check_form_wizard_backend()
        assert any(e.id == "next.E051" for e in errors)

    @override_settings(
        NEXT_FRAMEWORK={"DEFAULT_FORM_WIZARD_BACKEND": {"BACKEND": "no.such.Module"}}
    )
    def test_unimportable_backend_is_e051(self) -> None:
        """A path that fails to import surfaces an E051 error."""
        errors = check_form_wizard_backend()
        assert any(
            e.id == "next.E051" and "cannot be imported" in e.msg for e in errors
        )

    @override_settings(
        NEXT_FRAMEWORK={
            "DEFAULT_FORM_WIZARD_BACKEND": {
                "BACKEND": "next.forms.RegistryFormActionBackend"
            }
        }
    )
    def test_non_wizard_backend_class_is_e051(self) -> None:
        """A real class that is not a FormWizardBackend triggers E051."""
        errors = check_form_wizard_backend()
        assert len(errors) == 1
        assert errors[0].id == "next.E051"
        assert "FormWizardBackend" in errors[0].msg


def _without_sessions(installed_apps: list[str]) -> list[str]:
    return [app for app in installed_apps if app != "django.contrib.sessions"]


class TestCheckFormWizardSessions:
    """check_form_wizard_sessions: W056 for cache wizard storage without sessions."""

    def _register_wizard(self) -> None:
        form_action_manager.register_action(
            ActionRegistration(
                name="demo_wizard",
                file_path=_FAKE_FILE,
                scope="shared",
                wizard_class=type("DemoWizardStub", (), {}),
            )
        )

    def test_sessions_installed_is_clean(self) -> None:
        """No warning while django.contrib.sessions is installed."""
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_no_wizards_is_clean(self, settings, monkeypatch) -> None:
        """No warning when no wizard is registered."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        monkeypatch.setattr(
            form_action_manager, "_backends", [RegistryFormActionBackend()]
        )
        assert check_form_wizard_sessions() == []

    def test_non_string_backend_is_clean(self, settings) -> None:
        """A malformed BACKEND value is E051 territory, not W056."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        settings.NEXT_FRAMEWORK = {"DEFAULT_FORM_WIZARD_BACKEND": {"BACKEND": 7}}
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_unimportable_backend_is_clean(self, settings) -> None:
        """An unimportable BACKEND path is E051 territory, not W056."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_WIZARD_BACKEND": {"BACKEND": "no.such.Module"}
        }
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_non_cache_backend_is_clean(self, settings) -> None:
        """A custom backend not keyed by session passes without warning."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        settings.NEXT_FRAMEWORK = {
            "DEFAULT_FORM_WIZARD_BACKEND": {
                "BACKEND": "next.forms.RegistryFormActionBackend"
            }
        }
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_cache_backend_without_sessions_is_w056(self, settings) -> None:
        """The default cache backend without sessions produces W056."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        self._register_wizard()
        warnings = check_form_wizard_sessions()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W056"


class TestCheckFormAnchorFiles:
    """check_form_anchor_files: E052 for a malformed FORM_ANCHOR_FILES setting."""

    def test_settings_not_dict_yields_no_errors(self, settings) -> None:
        """`NEXT_FRAMEWORK` not being a dict short-circuits cleanly."""
        settings.NEXT_FRAMEWORK = "garbage"
        assert check_form_anchor_files() == []

    @override_settings(NEXT_FRAMEWORK={})
    def test_absent_value_is_clean(self) -> None:
        """An absent FORM_ANCHOR_FILES is valid and yields no errors."""
        assert check_form_anchor_files() == []

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": None})
    def test_none_value_is_clean(self) -> None:
        """An explicit None is valid and yields no errors."""
        assert check_form_anchor_files() == []

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": ["page.py", "view.py"]})
    def test_list_of_strings_is_clean(self) -> None:
        """A list of strings is valid and yields no errors."""
        assert check_form_anchor_files() == []

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": ("page.py",)})
    def test_tuple_of_strings_is_clean(self) -> None:
        """A tuple of strings is valid and yields no errors."""
        assert check_form_anchor_files() == []

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": "page.py"})
    def test_bare_string_is_e052(self) -> None:
        """A bare string is rejected so it never iterates into characters."""
        errors = check_form_anchor_files()
        assert len(errors) == 1
        assert errors[0].id == "next.E052"

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": 7})
    def test_non_collection_is_e052(self) -> None:
        """A non-collection value is rejected."""
        errors = check_form_anchor_files()
        assert len(errors) == 1
        assert errors[0].id == "next.E052"

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": ["page.py", 7]})
    def test_non_string_member_is_e052(self) -> None:
        """A collection with a non-string member is rejected."""
        errors = check_form_anchor_files()
        assert len(errors) == 1
        assert errors[0].id == "next.E052"
        assert "only strings" in errors[0].msg
