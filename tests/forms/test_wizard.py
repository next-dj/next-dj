from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest
from django import forms as django_forms
from django.contrib.sessions.backends.cache import SessionStore
from django.core.cache import caches
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, override_settings

from next.conf import next_framework_settings
from next.forms import Form
from next.forms.base import _FRAMEWORK_ROOT, _outside_base_dir_classes
from next.forms.manager import form_action_manager
from next.forms.wizard import (
    CacheFormWizardBackend,
    FormWizard,
    FormWizardBackend,
    WizardBackendManager,
    _ensure_session_key,
    _replace_step_segment,
    _wizard_without_steps,
    clear_wizard_registration_state,
    wizard_backend_manager,
)


class IdentityStep(Form):
    """First wizard step capturing a name."""

    name = django_forms.CharField(max_length=100)


class ScopeStep(Form):
    """Second wizard step capturing a scope."""

    scope = django_forms.CharField(max_length=100)


class ApprovalStep(Form):
    """Final wizard step capturing an approver."""

    approver = django_forms.CharField(max_length=100)


class OptionalStep(Form):
    """Conditional wizard step toggled by earlier answers."""

    detail = django_forms.CharField(max_length=100)


class DemoWizard(FormWizard):
    """Three-step wizard storing drafts through the cache backend."""

    class Meta:
        """Three ordered steps with the default URL parameter."""

        steps: ClassVar = [
            ("identity", IdentityStep),
            ("scope", ScopeStep),
            ("approval", ApprovalStep),
        ]
        url_param = "step"

    done_payloads: ClassVar[list] = []

    def done(self, request, cleaned_data) -> HttpResponse:
        """Record the merged cleaned data and return a redirect."""
        type(self).done_payloads.append(cleaned_data)
        return HttpResponseRedirect("/done/")


def _request(*, path: str = "/wizard/identity/"):
    """Return a POST request with a cache-backed session attached."""
    request = RequestFactory().post(path)
    request.session = SessionStore()
    return request


class TestReplaceStepSegment:
    """`_replace_step_segment`: empty path, found segment, and fallback."""

    def test_empty_path_returns_path(self) -> None:
        """An empty path is returned unchanged."""
        assert _replace_step_segment("", "identity", "scope") == ""

    def test_swaps_matching_segment(self) -> None:
        """The segment naming the current step is replaced with the target."""
        result = _replace_step_segment("/request/identity/", "identity", "scope")
        assert result == "/request/scope/"

    def test_falls_back_to_last_nonempty_segment(self) -> None:
        """When the current step is absent, the last real segment is replaced."""
        result = _replace_step_segment("/request/landing/", "identity", "scope")
        assert result == "/request/scope/"

    def test_all_empty_segments_returns_path(self) -> None:
        """A path with no non-empty segments is returned unchanged."""
        assert _replace_step_segment("/", "identity", "scope") == "/"


class TestWizardRegistration:
    """`FormWizard.__init_subclass__` registers wizards as one action."""

    def test_auto_name_is_snake_case_of_class(self) -> None:
        """A wizard registers under the snake_case of its class name."""
        meta = form_action_manager.default_backend.get_meta("demo_wizard")
        assert meta is not None
        assert meta["wizard_class"] is DemoWizard

    def test_abstract_meta_skips_registration(self) -> None:
        """A wizard with Meta.abstract = True is not registered."""

        class AbstractWizard(FormWizard):
            class Meta:
                abstract = True
                steps: ClassVar = []

        assert form_action_manager.default_backend.get_meta("abstract_wizard") is None

    def test_empty_steps_records_wizard_without_steps(self) -> None:
        """A wizard with empty Meta.steps is flagged for the E050 check."""

        class SteplessWizard(FormWizard):
            class Meta:
                steps: ClassVar = []

        assert SteplessWizard.__qualname__ in _wizard_without_steps

    def test_invalid_scope_skips_registration(self) -> None:
        """A wizard with an invalid Meta.scope is not registered."""

        class BadScopeWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", IdentityStep)]
                scope = "global"

        assert form_action_manager.default_backend.get_meta("bad_scope_wizard") is None

    def test_explicit_shared_scope_registers(self) -> None:
        """A wizard with Meta.scope = 'shared' registers under that scope."""

        class SharedScopeWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", IdentityStep)]
                scope = "shared"

        meta = form_action_manager.default_backend.get_meta("shared_scope_wizard")
        assert meta is not None
        assert meta["scope"] == "shared"

    def test_clear_registration_state_empties_lists(self) -> None:
        """`clear_wizard_registration_state` drops accumulated registration state."""
        snapshot_without = list(_wizard_without_steps)
        clear_wizard_registration_state()
        assert _wizard_without_steps == []
        _wizard_without_steps.extend(snapshot_without)

    def test_virtual_path_skips_registration(self) -> None:
        """A wizard defined in a virtual frame is not registered."""
        with patch("next.forms.wizard._find_definition_frame", return_value="<stdin>"):

            class VirtualWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert form_action_manager.default_backend.get_meta("virtual_wizard") is None

    def test_empty_path_skips_registration(self) -> None:
        """A wizard whose definition frame is empty is not registered."""
        with patch("next.forms.wizard._find_definition_frame", return_value=""):

            class EmptyPathWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert form_action_manager.default_backend.get_meta("empty_path_wizard") is None

    def test_framework_file_skips_registration(self) -> None:
        """A wizard defined inside the framework package is not registered."""
        framework_path = str(_FRAMEWORK_ROOT / "forms" / "wizard.py")
        with patch(
            "next.forms.wizard._find_definition_frame", return_value=framework_path
        ):

            class FrameworkWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert form_action_manager.default_backend.get_meta("framework_wizard") is None

    def test_outside_base_dir_records_warning(self, settings, tmp_path) -> None:
        """A wizard outside BASE_DIR is flagged for W046 and not registered."""
        settings.BASE_DIR = tmp_path / "project_root"
        outside = tmp_path / "outside"
        outside.mkdir()
        fake_path = str(outside / "wizards.py")
        Path(fake_path).write_text("")

        with patch("next.forms.wizard._find_definition_frame", return_value=fake_path):

            class OutsideWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert any("OutsideWizard" in name for name, _ in _outside_base_dir_classes)
        assert form_action_manager.default_backend.get_meta("outside_wizard") is None


class TestWizardStepIntrospection:
    """`step_names`, `current_step`, `is_first`, `is_last`, `next_step`."""

    def test_step_names_in_declaration_order(self) -> None:
        """`step_names` returns names in the order steps were declared."""
        wizard = DemoWizard(_request())
        assert wizard.step_names() == ["identity", "scope", "approval"]

    def test_current_step_defaults_to_first(self) -> None:
        """With no URL kwarg, the current step is the first step."""
        wizard = DemoWizard(_request())
        assert wizard.current_step() == "identity"

    def test_current_step_reads_url_kwarg(self) -> None:
        """A valid url_param kwarg selects the matching step."""
        wizard = DemoWizard(_request(), url_kwargs={"step": "scope"})
        assert wizard.current_step() == "scope"

    def test_current_step_invalid_kwarg_falls_back_to_first(self) -> None:
        """An unknown step value falls back to the first step."""
        wizard = DemoWizard(_request(), url_kwargs={"step": "ghost"})
        assert wizard.current_step() == "identity"

    def test_current_step_non_string_kwarg_falls_back(self) -> None:
        """A non-string step value falls back to the first step."""
        wizard = DemoWizard(_request(), url_kwargs={"step": 7})
        assert wizard.current_step() == "identity"

    def test_current_step_matches_int_coerced_kwarg(self) -> None:
        """A url kwarg coerced to int still resolves to its string step name."""

        class NumericStepWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("1", IdentityStep), ("2", ScopeStep)]

            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponseRedirect("/done/")

        wizard = NumericStepWizard(_request(), url_kwargs={"step": 2})
        assert wizard.current_step() == "2"

    def test_is_first_on_first_step(self) -> None:
        """`is_first` is True on the first step and False elsewhere."""
        assert DemoWizard(_request()).is_first() is True
        assert DemoWizard(_request(), url_kwargs={"step": "scope"}).is_first() is False

    def test_is_last_on_last_step(self) -> None:
        """`is_last` is True only on the last step."""
        assert DemoWizard(_request()).is_last() is False
        assert DemoWizard(_request(), url_kwargs={"step": "approval"}).is_last() is True

    def test_next_step_advances(self) -> None:
        """`next_step` returns the following step name."""
        wizard = DemoWizard(_request())
        assert wizard.next_step("identity") == "scope"
        assert wizard.next_step("scope") == "approval"

    def test_next_step_on_last_returns_none(self) -> None:
        """`next_step` returns None past the last step."""
        wizard = DemoWizard(_request())
        assert wizard.next_step("approval") is None

    def test_next_step_unknown_returns_none(self) -> None:
        """`next_step` returns None for an unknown step."""
        wizard = DemoWizard(_request())
        assert wizard.next_step("ghost") is None

    def test_next_step_defaults_to_current(self) -> None:
        """`next_step` with no argument advances from the current step."""
        wizard = DemoWizard(_request(), url_kwargs={"step": "scope"})
        assert wizard.next_step() == "approval"


class TestWizardGoto:
    """`goto` rewrites the page path segment for a target step."""

    def test_goto_swaps_current_segment(self) -> None:
        """`goto` rewrites the current-step segment to the target step."""
        wizard = DemoWizard(
            _request(),
            url_kwargs={"step": "identity"},
            base_path="/request/identity/",
        )
        assert wizard.goto("scope") == "/request/scope/"

    def test_goto_falls_back_when_current_absent_from_path(self) -> None:
        """`goto` rewrites the last segment when the current step is absent."""
        wizard = DemoWizard(
            _request(),
            url_kwargs={"step": "identity"},
            base_path="/request/landing/",
        )
        assert wizard.goto("scope") == "/request/scope/"


class TestWizardStorageInteraction:
    """`save_step`, `cleaned_data_so_far`, `completed_steps`, `clear_storage`."""

    def test_save_step_then_cleaned_data_so_far_merges(self) -> None:
        """Saved steps merge into a single cleaned-data mapping read through cache."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        wizard.save_step("scope", {"scope": "ops"})
        assert wizard.cleaned_data_so_far() == {"name": "Ada", "scope": "ops"}

    def test_completed_steps_lists_saved_steps(self) -> None:
        """`completed_steps` reports the saved step names in order."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        assert wizard.completed_steps() == ["identity"]

    def test_clear_storage_drops_saved_steps(self) -> None:
        """`clear_storage` removes every saved step."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        wizard.clear_storage()
        assert wizard.cleaned_data_so_far() == {}
        assert wizard.completed_steps() == []

    def test_wizard_id_and_url_param_defaults(self) -> None:
        """The wizard exposes its derived id and URL parameter."""
        wizard = DemoWizard(_request())
        assert wizard.url_param == "step"
        assert wizard.wizard_id == "demo_wizard"


class TestWizardFormResolution:
    """`step_form_class`, `current_form`, `template_namespace`."""

    def test_step_form_class_resolves_by_name(self) -> None:
        """`step_form_class` resolves the form class registered for a step."""
        wizard = DemoWizard(_request())
        assert wizard.step_form_class("scope") is ScopeStep

    def test_step_form_class_defaults_to_current(self) -> None:
        """`step_form_class` with no argument uses the current step."""
        wizard = DemoWizard(_request(), url_kwargs={"step": "scope"})
        assert wizard.step_form_class() is ScopeStep

    def test_current_form_is_unbound_instance(self) -> None:
        """`current_form` returns an unbound instance of the step form."""
        wizard = DemoWizard(_request())
        form = wizard.current_form()
        assert isinstance(form, IdentityStep)
        assert form.is_bound is False

    def test_current_form_prefills_from_storage(self) -> None:
        """`current_form` prefills initial values from previously saved data."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        form = DemoWizard(request).current_form()
        assert form.initial == {"name": "Ada"}

    def test_current_form_none_when_no_class(self) -> None:
        """`current_form` returns None when no class backs the current step."""

        class EmptyWizard(FormWizard):
            class Meta:
                steps: ClassVar = []

            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponse("ok")

        assert EmptyWizard(_request()).current_form() is None

    def test_template_namespace_exposes_form_and_wizard(self) -> None:
        """`template_namespace` carries the current form and the wizard."""
        wizard = DemoWizard(_request())
        namespace = wizard.template_namespace()
        assert isinstance(namespace.form, IdentityStep)
        assert namespace.wizard is wizard


class TestWizardHooks:
    """`steps_for` and `get_form_kwargs` override hooks."""

    def test_steps_for_default_returns_static_steps(self) -> None:
        """The default `steps_for` returns the declared steps."""
        wizard = DemoWizard(_request())
        assert [name for name, _ in wizard.steps_for()] == [
            "identity",
            "scope",
            "approval",
        ]

    def test_steps_for_override_adds_conditional_step(self) -> None:
        """A `steps_for` override can insert a conditional step."""

        class ConditionalWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", IdentityStep), ("scope", ScopeStep)]

            def steps_for(self):
                base = [("identity", IdentityStep), ("scope", ScopeStep)]
                if self.cleaned_data_so_far().get("name") == "needs-detail":
                    base.insert(1, ("optional", OptionalStep))
                return base

            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponse("ok")

        request = _request()
        plain = ConditionalWizard(request)
        assert plain.step_names() == ["identity", "scope"]

        plain.save_step("identity", {"name": "needs-detail"})
        expanded = ConditionalWizard(request)
        assert expanded.step_names() == ["identity", "optional", "scope"]

    def test_get_form_kwargs_default_is_empty(self) -> None:
        """The default `get_form_kwargs` returns an empty mapping."""
        wizard = DemoWizard(_request())
        assert wizard.get_form_kwargs() == {}

    def test_get_form_kwargs_override_feeds_current_form(self) -> None:
        """A `get_form_kwargs` override flows into the constructed step form."""

        class PrefixField(Form):
            label = django_forms.CharField(max_length=50)

        class KwargsWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("only", PrefixField)]

            def get_form_kwargs(self):
                return {"prefix": "wiz"}

            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponse("ok")

        form = KwargsWizard(_request()).current_form()
        assert form.prefix == "wiz"


class TestWizardDoneContract:
    """The base `done` raises until a subclass overrides it."""

    def test_base_done_raises_not_implemented(self) -> None:
        """`FormWizard.done` is abstract and raises NotImplementedError."""

        class NoDoneWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", IdentityStep)]

        wizard = NoDoneWizard(_request())
        with pytest.raises(NotImplementedError, match="must implement done"):
            wizard.done(_request(), {})

    def test_base_path_defaults_to_request_path(self) -> None:
        """With no explicit base_path, the wizard uses the request path."""
        request = _request(path="/wizard/identity/")
        wizard = DemoWizard(request)
        assert wizard.base_path == "/wizard/identity/"


class TestEnsureSessionKey:
    """`_ensure_session_key`: missing session, lazy create, and bare request."""

    def test_no_session_attribute_returns_empty(self) -> None:
        """A request without a `session` attribute yields an empty key."""
        request = RequestFactory().get("/")
        assert _ensure_session_key(request, create=True) == ""

    def test_create_false_without_session_returns_empty(self) -> None:
        """With no session yet and create=False, the key stays empty."""
        request = _request()
        assert _ensure_session_key(request, create=False) == ""
        assert request.session.session_key is None

    def test_create_true_creates_session(self) -> None:
        """With create=True, a session is created and its key returned."""
        request = _request()
        key = _ensure_session_key(request, create=True)
        assert key
        assert request.session.session_key == key


class TestCacheFormWizardBackend:
    """`CacheFormWizardBackend`: round-trip, namespacing, alias, timeout, laziness."""

    def test_save_load_clear_round_trip(self) -> None:
        """save_step stores per-step data that load returns and clear drops."""
        backend = CacheFormWizardBackend()
        request = _request()
        backend.save_step(request, "wiz", "identity", {"name": "Ada"})
        backend.save_step(request, "wiz", "scope", {"scope": "ops"})
        assert backend.load(request, "wiz") == {
            "identity": {"name": "Ada"},
            "scope": {"scope": "ops"},
        }
        backend.clear(request, "wiz")
        assert backend.load(request, "wiz") == {}

    def test_distinct_sessions_are_isolated(self) -> None:
        """Two requests with different sessions never see each other's drafts."""
        backend = CacheFormWizardBackend()
        first = _request()
        second = _request()
        backend.save_step(first, "wiz", "identity", {"name": "Ada"})
        assert backend.load(second, "wiz") == {}

    def test_load_without_session_returns_empty_and_creates_nothing(self) -> None:
        """`load` on a session-less request returns {} without creating a session."""
        backend = CacheFormWizardBackend()
        request = _request()
        assert backend.load(request, "wiz") == {}
        assert request.session.session_key is None

    def test_clear_without_session_is_a_noop(self) -> None:
        """`clear` on a session-less request leaves the session untouched."""
        backend = CacheFormWizardBackend()
        request = _request()
        backend.clear(request, "wiz")
        assert request.session.session_key is None

    def test_save_step_creates_session(self) -> None:
        """`save_step` creates a session so anonymous drafts have a key."""
        backend = CacheFormWizardBackend()
        request = _request()
        backend.save_step(request, "wiz", "identity", {"name": "Ada"})
        assert request.session.session_key is not None

    @override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "wizard-default",
            },
            "alt": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "wizard-alt",
            },
        }
    )
    def test_cache_alias_option_routes_to_named_cache(self) -> None:
        """An OPTIONS CACHE_ALIAS routes storage to the named cache only."""
        caches["default"].clear()
        caches["alt"].clear()
        backend = CacheFormWizardBackend({"OPTIONS": {"CACHE_ALIAS": "alt"}})
        request = _request()
        backend.save_step(request, "wiz", "identity", {"name": "Ada"})
        session_key = request.session.session_key
        key = f"next_wizard:{session_key}:wiz"
        assert caches["alt"].get(key) == {"identity": {"name": "Ada"}}
        assert caches["default"].get(key) is None

    def test_timeout_option_is_passed_to_cache_set(self) -> None:
        """An OPTIONS TIMEOUT flows into the cache set call."""
        backend = CacheFormWizardBackend({"OPTIONS": {"TIMEOUT": 321}})
        request = _request()
        with patch.object(backend, "_cache") as cache_factory:
            backend.save_step(request, "wiz", "identity", {"name": "Ada"})
        cache_factory.return_value.set.assert_called_once()
        assert cache_factory.return_value.set.call_args.args[2] == 321

    def test_timeout_defaults_to_session_cookie_age(self, settings) -> None:
        """Without an OPTIONS TIMEOUT, `_timeout` falls back to SESSION_COOKIE_AGE."""
        settings.SESSION_COOKIE_AGE = 1234
        backend = CacheFormWizardBackend({"OPTIONS": {}})
        assert backend._timeout() == 1234

    def test_timeout_option_none_is_preserved(self) -> None:
        """An explicit None TIMEOUT is preserved over SESSION_COOKIE_AGE."""
        backend = CacheFormWizardBackend({"OPTIONS": {"TIMEOUT": None}})
        assert backend._timeout() is None

    def test_non_dict_config_uses_defaults(self) -> None:
        """A non-dict config falls back to the default cache alias."""
        backend = CacheFormWizardBackend("not-a-dict")
        assert backend.cache_alias == "default"

    def test_non_dict_options_uses_defaults(self) -> None:
        """A non-dict OPTIONS value falls back to the default cache alias."""
        backend = CacheFormWizardBackend({"OPTIONS": "garbage"})
        assert backend.cache_alias == "default"


class _StubWizardBackend(FormWizardBackend):
    """Minimal backend recording the config it was constructed with."""

    instances: ClassVar[list] = []

    def __init__(self, config=None) -> None:
        type(self).instances.append(config)

    def load(self, request, wizard_id):
        return {}

    def save_step(self, request, wizard_id, step, data) -> None:
        return None

    def clear(self, request, wizard_id) -> None:
        return None


class TestWizardBackendManager:
    """`WizardBackendManager`: caching, reset, custom backend, and reload hook."""

    def test_get_returns_default_backend_and_caches(self) -> None:
        """`get` instantiates the configured backend once and caches it."""
        manager = WizardBackendManager()
        first = manager.get()
        second = manager.get()
        assert isinstance(first, CacheFormWizardBackend)
        assert first is second

    def test_reset_forces_reinstantiation(self) -> None:
        """`reset` drops the cached backend so the next `get` rebuilds it."""
        manager = WizardBackendManager()
        first = manager.get()
        manager.reset()
        assert manager.get() is not first

    def test_custom_backend_from_settings_is_used(self) -> None:
        """A configured custom backend path is imported and constructed with config."""
        _StubWizardBackend.instances.clear()
        config = {
            "BACKEND": f"{__name__}._StubWizardBackend",
            "OPTIONS": {"flag": True},
        }
        with override_settings(NEXT_FRAMEWORK={"DEFAULT_FORM_WIZARD_BACKEND": config}):
            next_framework_settings.reload()
            manager = WizardBackendManager()
            backend = manager.get()
        assert isinstance(backend, _StubWizardBackend)
        assert _StubWizardBackend.instances[-1]["OPTIONS"] == {"flag": True}

    def test_settings_reloaded_signal_resets_global_manager(self) -> None:
        """The settings_reloaded signal drops the shared manager's backend."""
        first = wizard_backend_manager.get()
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_FORM_WIZARD_BACKEND": {
                    "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
                    "OPTIONS": {},
                }
            }
        ):
            next_framework_settings.reload()
            assert wizard_backend_manager.get() is not first
