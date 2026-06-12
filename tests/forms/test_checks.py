from typing import ClassVar

import pytest
from django import forms as django_forms
from django.test import override_settings

from next.forms import ActionRegistration, FormWizard, RegistryFormActionBackend
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
    check_shared_action_name_collisions,
    check_wizard_step_actions,
    check_wizard_step_field_collisions,
    check_wizard_step_file_fields,
)
from next.forms.decorators import action
from next.forms.diagnostics import registration_diagnostics
from next.forms.manager import form_action_manager
from next.forms.signals import action_registered


_FAKE_FILE = "/fake/myapp/forms.py"


@pytest.fixture(autouse=True)
def _reset_collision_cache():
    registration_diagnostics.action_collisions.clear()
    registration_diagnostics.shared_name_collisions.clear()
    yield
    registration_diagnostics.action_collisions.clear()
    registration_diagnostics.shared_name_collisions.clear()


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
        assert "move one to a different scope" in errors[0].msg

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


class TestSharedActionNameCollisions:
    """check_shared_action_name_collisions: E046 when one shared name spans modules."""

    @staticmethod
    def _app_forms_file(tmp_path, app: str) -> str:
        app_dir = tmp_path / app
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        forms_file = app_dir / "forms.py"
        forms_file.write_text("")
        return str(forms_file)

    @staticmethod
    def _register(
        backend: RegistryFormActionBackend, name: str, file_path: str, scope: str
    ) -> None:
        backend.register_action(
            ActionRegistration(
                name=name,
                file_path=file_path,
                scope=scope,
                handler=lambda: None,
            )
        )

    def test_same_shared_name_in_two_modules_is_e046(self, tmp_path) -> None:
        """Two shared-scope declarations of one name produce a single E046."""
        backend = RegistryFormActionBackend()
        self._register(
            backend, "contact_form", self._app_forms_file(tmp_path, "app_a"), "shared"
        )
        self._register(
            backend, "contact_form", self._app_forms_file(tmp_path, "app_b"), "shared"
        )
        errors = check_shared_action_name_collisions()
        assert len(errors) == 1
        assert errors[0].id == "next.E046"
        assert "'contact_form'" in errors[0].msg
        assert "app_a.forms" in errors[0].msg
        assert "app_b.forms" in errors[0].msg
        assert "Meta.scope" in errors[0].msg

    def test_third_module_joins_the_same_error(self, tmp_path) -> None:
        """A third declaring module folds into the existing E046 entry."""
        backend = RegistryFormActionBackend()
        for app in ("app_a", "app_b", "app_c"):
            self._register(
                backend, "contact_form", self._app_forms_file(tmp_path, app), "shared"
            )
        errors = check_shared_action_name_collisions()
        assert len(errors) == 1
        assert "3 modules" in errors[0].msg
        assert "app_c.forms" in errors[0].msg

    @pytest.mark.parametrize(
        ("first_scope", "second_scope", "second_app"),
        [
            pytest.param("shared", "shared", None, id="same-module-reregistration"),
            pytest.param(
                "shared", "page", "app_b", id="second-registration-page-scoped"
            ),
            pytest.param(
                "page", "shared", "app_b", id="first-registration-page-scoped"
            ),
            pytest.param("page", "page", "app_b", id="two-page-scopes"),
        ],
    )
    def test_silent_registrations(
        self, tmp_path, first_scope: str, second_scope: str, second_app: str | None
    ) -> None:
        """Re-registration and page-scoped overlaps never reach the buffer."""
        backend = RegistryFormActionBackend()
        first = self._app_forms_file(tmp_path, "app_a")
        second = (
            first if second_app is None else self._app_forms_file(tmp_path, second_app)
        )
        self._register(backend, "contact_form", first, first_scope)
        self._register(backend, "contact_form", second, second_scope)
        assert registration_diagnostics.shared_name_collisions == {}
        assert check_shared_action_name_collisions() == []


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
        settings.NEXT_FRAMEWORK = {"FORM_ACTION_BACKENDS": "x"}
        errors = check_form_action_backends_configuration()
        assert len(errors) == 1
        assert errors[0].id == "next.E044"

    def test_non_dict_entry_is_e044(self, settings) -> None:
        """Each entry must be a dict."""
        settings.NEXT_FRAMEWORK = {"FORM_ACTION_BACKENDS": ["nope"]}
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E044" for e in errors)

    def test_non_string_backend_is_e044(self, settings) -> None:
        """`BACKEND` must be a string."""
        settings.NEXT_FRAMEWORK = {"FORM_ACTION_BACKENDS": [{"BACKEND": 7}]}
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E044" for e in errors)

    def test_unimportable_backend_is_e044(self, settings) -> None:
        """A path that fails to import surfaces the original error."""
        settings.NEXT_FRAMEWORK = {
            "FORM_ACTION_BACKENDS": [{"BACKEND": "no.such.Module"}],
        }
        errors = check_form_action_backends_configuration()
        assert any(
            e.id == "next.E044" and "cannot be imported" in e.msg for e in errors
        )

    def test_wrong_type_backend_is_e045(self, settings) -> None:
        """A class that is not a `FormActionBackend` subclass triggers E045."""
        settings.NEXT_FRAMEWORK = {
            "FORM_ACTION_BACKENDS": [{"BACKEND": "django.http.HttpResponse"}],
        }
        errors = check_form_action_backends_configuration()
        assert any(e.id == "next.E045" for e in errors)

    def test_valid_default_backend_is_clean(self, settings) -> None:
        """Default backend path passes the check without errors."""
        settings.NEXT_FRAMEWORK = {
            "FORM_ACTION_BACKENDS": [
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

    def test_invalid_action_scope_triggers_error(self) -> None:
        """An @action with an invalid scope produces an E047 error."""
        registration_diagnostics.invalid_action_scope.clear()
        registration_diagnostics.invalid_action_scope.append(
            ("bad_scope_handler", "global")
        )
        errors = check_invalid_form_meta_scope()
        assert len(errors) == 1
        assert errors[0].id == "next.E047"
        assert "Action 'bad_scope_handler'" in errors[0].msg
        assert "global" in errors[0].msg
        registration_diagnostics.invalid_action_scope.clear()


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

    def test_applying_action_to_class_surfaces_through_check(self) -> None:
        """@action on a class returns it untouched and the E053 check fires."""
        registration_diagnostics.action_applied_to_class.clear()

        @action("bad_class")
        class BadTargetClass:
            pass

        assert isinstance(BadTargetClass, type)
        errors = check_action_applied_to_class()
        assert len(errors) == 1
        assert errors[0].id == "next.E053"
        assert "BadTargetClass" in errors[0].msg
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
    """check_form_wizard_backend: E051 for a malformed FORM_WIZARD_BACKEND."""

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
            "FORM_WIZARD_BACKEND": {
                "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
                "OPTIONS": {},
            }
        }
    )
    def test_default_config_is_clean(self) -> None:
        """The default cache backend config passes the check."""
        assert check_form_wizard_backend() == []

    @override_settings(NEXT_FRAMEWORK={"FORM_WIZARD_BACKEND": ["not", "a", "dict"]})
    def test_non_dict_config_is_e051(self) -> None:
        """A non-dict config triggers E051."""
        errors = check_form_wizard_backend()
        assert len(errors) == 1
        assert errors[0].id == "next.E051"

    @override_settings(NEXT_FRAMEWORK={"FORM_WIZARD_BACKEND": {"BACKEND": 7}})
    def test_non_string_backend_is_e051(self) -> None:
        """`BACKEND` must be a string."""
        errors = check_form_wizard_backend()
        assert any(e.id == "next.E051" for e in errors)

    @override_settings(
        NEXT_FRAMEWORK={"FORM_WIZARD_BACKEND": {"BACKEND": "no.such.Module"}}
    )
    def test_unimportable_backend_is_e051(self) -> None:
        """A path that fails to import surfaces an E051 error."""
        errors = check_form_wizard_backend()
        assert any(
            e.id == "next.E051" and "cannot be imported" in e.msg for e in errors
        )

    @override_settings(
        NEXT_FRAMEWORK={
            "FORM_WIZARD_BACKEND": {"BACKEND": "next.forms.RegistryFormActionBackend"}
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
    """check_form_wizard_sessions: W056 for session-bound storage without sessions."""

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
        settings.NEXT_FRAMEWORK = {"FORM_WIZARD_BACKEND": {"BACKEND": 7}}
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_unimportable_backend_is_clean(self, settings) -> None:
        """An unimportable BACKEND path is E051 territory, not W056."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        settings.NEXT_FRAMEWORK = {"FORM_WIZARD_BACKEND": {"BACKEND": "no.such.Module"}}
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_non_cache_backend_is_clean(self, settings) -> None:
        """A custom backend not keyed by session passes without warning."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        settings.NEXT_FRAMEWORK = {
            "FORM_WIZARD_BACKEND": {"BACKEND": "next.forms.RegistryFormActionBackend"}
        }
        self._register_wizard()
        assert check_form_wizard_sessions() == []

    def test_default_backend_without_sessions_is_w056(self, settings) -> None:
        """The default session backend without sessions produces W056."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        self._register_wizard()
        warnings = check_form_wizard_sessions()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W056"

    def test_cache_backend_without_sessions_is_w056(self, settings) -> None:
        """The cache backend keys by session and warns without sessions too."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        settings.NEXT_FRAMEWORK = {
            "FORM_WIZARD_BACKEND": {
                "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
                "OPTIONS": {},
            }
        }
        self._register_wizard()
        warnings = check_form_wizard_sessions()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W056"

    def test_second_backend_wizard_is_visible(self, settings, monkeypatch) -> None:
        """A wizard owned by a non-default backend still triggers W056."""
        settings.INSTALLED_APPS = _without_sessions(settings.INSTALLED_APPS)
        first = RegistryFormActionBackend()
        second = RegistryFormActionBackend()
        second.register_action(
            ActionRegistration(
                name="second_backend_wizard",
                file_path=_FAKE_FILE,
                scope="shared",
                wizard_class=type("DemoWizardStub", (), {}),
            )
        )
        monkeypatch.setattr(form_action_manager, "_backends", [first, second])
        warnings = check_form_wizard_sessions()
        assert [w.id for w in warnings] == ["next.W056"]


class _W057StepForm(django_forms.Form):
    """Plain step form registered manually as a standalone action in tests."""

    name = django_forms.CharField()


class _W057Wizard(FormWizard):
    """Wizard kept out of auto-registration and registered manually in tests."""

    class Meta:
        """One static step pointing at the shared test form."""

        abstract = True
        steps: ClassVar = [("identity", _W057StepForm)]


class TestCheckWizardStepActions:
    """check_wizard_step_actions: W057 when a step doubles as a standalone action."""

    def _isolated_backend(self, monkeypatch) -> RegistryFormActionBackend:
        backend = RegistryFormActionBackend()
        monkeypatch.setattr(form_action_manager, "_backends", [backend])
        return backend

    def _register(
        self, backend: RegistryFormActionBackend, *, with_step_action: bool
    ) -> None:
        if with_step_action:
            backend.register_action(
                ActionRegistration(
                    name="w057_step_form",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    form_class=_W057StepForm,
                )
            )
        backend.register_action(
            ActionRegistration(
                name="w057_wizard",
                file_path=_FAKE_FILE,
                scope="shared",
                wizard_class=_W057Wizard,
            )
        )

    def test_step_registered_as_action_is_w057(self, monkeypatch) -> None:
        """A step class living in the action registry produces W057."""
        backend = self._isolated_backend(monkeypatch)
        self._register(backend, with_step_action=True)
        warnings = check_wizard_step_actions()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W057"
        assert "_W057StepForm" in warnings[0].msg
        assert "w057_step_form" in warnings[0].msg

    def test_unregistered_step_is_clean(self, monkeypatch) -> None:
        """A step class absent from the action registry yields no warning."""
        backend = self._isolated_backend(monkeypatch)
        self._register(backend, with_step_action=False)
        assert check_wizard_step_actions() == []

    def test_wizard_class_without_static_steps_is_skipped(self, monkeypatch) -> None:
        """A wizard stub without `_static_steps` is ignored by the check."""
        backend = self._isolated_backend(monkeypatch)
        backend.register_action(
            ActionRegistration(
                name="w057_stub_wizard",
                file_path=_FAKE_FILE,
                scope="shared",
                wizard_class=type("StubWizard", (), {}),
            )
        )
        assert check_wizard_step_actions() == []

    def test_factory_form_class_is_ignored(self, monkeypatch) -> None:
        """A callable form factory never matches a step class."""
        backend = self._isolated_backend(monkeypatch)
        backend.register_action(
            ActionRegistration(
                name="w057_factory",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
                form_class=lambda: _W057StepForm,
            )
        )
        self._register(backend, with_step_action=False)
        assert check_wizard_step_actions() == []


class _FileStep(django_forms.Form):
    """Step form carrying a FileField."""

    doc = django_forms.FileField()


class _ImageStep(django_forms.Form):
    """Step form carrying an ImageField."""

    photo = django_forms.ImageField()


class _PlainStep(django_forms.Form):
    """Step form without file fields."""

    name = django_forms.CharField()


class _FileStepsWizard(FormWizard):
    """Wizard mixing file-carrying and plain static steps."""

    class Meta:
        """Two file-carrying steps and one clean step."""

        abstract = True
        steps: ClassVar = [
            ("docs", _FileStep),
            ("photo", _ImageStep),
            ("name", _PlainStep),
        ]


class _CleanStepsWizard(FormWizard):
    """Wizard whose static steps carry no file fields."""

    class Meta:
        """One clean step."""

        abstract = True
        steps: ClassVar = [("name", _PlainStep)]


def _isolated_backend_with(
    monkeypatch, *registrations: ActionRegistration
) -> RegistryFormActionBackend:
    backend = RegistryFormActionBackend()
    for registration in registrations:
        backend.register_action(registration)
    monkeypatch.setattr(form_action_manager, "_backends", [backend])
    return backend


def _wizard_registration(name: str, wizard_class: type) -> ActionRegistration:
    return ActionRegistration(
        name=name,
        file_path=_FAKE_FILE,
        scope="shared",
        wizard_class=wizard_class,
    )


class _StepLessWizardStub:
    """Wizard stand-in without the `_static_steps` hook."""


class TestCheckWizardStepFileFields:
    """check_wizard_step_file_fields: W058 for FileField inside static steps."""

    def test_file_and_image_steps_each_warn(self, monkeypatch) -> None:
        """FileField and ImageField steps each produce one W058 warning."""
        _isolated_backend_with(
            monkeypatch, _wizard_registration("file_wizard", _FileStepsWizard)
        )
        warnings = check_wizard_step_file_fields()
        assert [w.id for w in warnings] == ["next.W058", "next.W058"]
        assert "'docs'" in warnings[0].msg
        assert "'photo'" in warnings[1].msg
        assert "_FileStepsWizard" in warnings[0].msg
        assert "do not survive" in warnings[0].msg

    @pytest.mark.parametrize(
        "registration",
        [
            pytest.param(
                _wizard_registration("clean_wizard", _CleanStepsWizard),
                id="steps-without-file-fields",
            ),
            pytest.param(
                _wizard_registration("stub_wizard", _StepLessWizardStub),
                id="wizard-stub-without-static-steps",
            ),
            pytest.param(
                ActionRegistration(
                    name="plain_action",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    form_class=_PlainStep,
                ),
                id="non-wizard-action",
            ),
        ],
    )
    def test_silent_registrations(
        self, monkeypatch, registration: ActionRegistration
    ) -> None:
        """Clean steps, step-less stubs, and plain actions yield no warnings."""
        _isolated_backend_with(monkeypatch, registration)
        assert check_wizard_step_file_fields() == []


class _EmailStepA(django_forms.Form):
    """First step declaring the shared `email` field."""

    email = django_forms.EmailField()
    full_name = django_forms.CharField()


class _EmailStepB(django_forms.Form):
    """Second step re-declaring the shared `email` field."""

    email = django_forms.EmailField()
    team = django_forms.CharField()


class _CollidingWizard(FormWizard):
    """Wizard whose static steps both declare `email`."""

    class Meta:
        """Two steps sharing one field name."""

        abstract = True
        steps: ClassVar = [("identity", _EmailStepA), ("scope", _EmailStepB)]


class _DisjointWizard(FormWizard):
    """Wizard whose static steps declare disjoint fields."""

    class Meta:
        """Two steps without shared field names."""

        abstract = True
        steps: ClassVar = [("identity", _PlainStep), ("contact", _EmailStepB)]


class TestCheckWizardStepFieldCollisions:
    """check_wizard_step_field_collisions: W059 for shared step field names."""

    def test_colliding_field_warns_with_step_names(self, monkeypatch) -> None:
        """A field declared by two steps produces one W059 naming both."""
        _isolated_backend_with(
            monkeypatch, _wizard_registration("colliding_wizard", _CollidingWizard)
        )
        warnings = check_wizard_step_field_collisions()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W059"
        assert "_CollidingWizard" in warnings[0].msg
        assert "'email'" in warnings[0].msg
        assert "'identity', 'scope'" in warnings[0].msg
        assert "get_cleaned_data_for_step()" in warnings[0].msg

    @pytest.mark.parametrize(
        "registration",
        [
            pytest.param(
                _wizard_registration("disjoint_wizard", _DisjointWizard),
                id="steps-with-disjoint-fields",
            ),
            pytest.param(
                _wizard_registration("stub_wizard", _StepLessWizardStub),
                id="wizard-stub-without-static-steps",
            ),
            pytest.param(
                ActionRegistration(
                    name="plain_action",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    form_class=_PlainStep,
                ),
                id="non-wizard-action",
            ),
        ],
    )
    def test_silent_registrations(
        self, monkeypatch, registration: ActionRegistration
    ) -> None:
        """Disjoint steps, step-less stubs, and plain actions yield no warnings."""
        _isolated_backend_with(monkeypatch, registration)
        assert check_wizard_step_field_collisions() == []


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

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("page.py", id="bare-string"),
            pytest.param(("page.py",), id="tuple-not-merged"),
            pytest.param({"page.py"}, id="set-not-merged"),
            pytest.param(7, id="non-collection"),
        ],
    )
    def test_non_list_value_is_e052(self, value: object) -> None:
        """Only a list round-trips the settings merge, anything else is rejected."""
        with override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": value}):
            errors = check_form_anchor_files()
        assert len(errors) == 1
        assert errors[0].id == "next.E052"
        assert "list of strings" in errors[0].msg

    @override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": ["page.py", 7]})
    def test_non_string_member_is_e052(self) -> None:
        """A collection with a non-string member is rejected."""
        errors = check_form_anchor_files()
        assert len(errors) == 1
        assert errors[0].id == "next.E052"
        assert "only strings" in errors[0].msg
