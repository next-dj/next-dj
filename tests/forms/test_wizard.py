import datetime
import json
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django import forms as django_forms
from django.contrib.auth.models import User
from django.contrib.sessions.backends.cache import SessionStore
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, override_settings

from next.conf import next_framework_settings
from next.forms import Form
from next.forms.base import _FRAMEWORK_ROOT
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics
from next.forms.wizard import (
    CacheFormWizardBackend,
    FormWizard,
    FormWizardBackend,
    SessionFormWizardBackend,
    WizardBackendManager,
    _ensure_session_key,
    _replace_step_segment,
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
    """Three-step wizard storing drafts through the default session backend."""

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


class CountingStepsWizard(FormWizard):
    """Two-step wizard counting `get_steps` evaluations per instance."""

    class Meta:
        """Two ordered steps with the default URL parameter."""

        steps: ClassVar = [("identity", IdentityStep), ("scope", ScopeStep)]
        url_param = "step"

    def get_steps(self):
        """Count each evaluation and delegate to the declared steps."""
        self.get_steps_calls = getattr(self, "get_steps_calls", 0) + 1
        return super().get_steps()


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

    def test_concrete_subclass_of_abstract_base_registers(self) -> None:
        """A subclass inheriting Meta.abstract from its base still registers."""

        class AbstractFlowWizard(FormWizard):
            class Meta:
                abstract = True
                steps: ClassVar = [("identity", IdentityStep)]

        class ConcreteFlowWizard(AbstractFlowWizard):
            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponse("ok")

        backend = form_action_manager.default_backend
        assert backend.get_meta("abstract_flow_wizard") is None
        meta = backend.get_meta("concrete_flow_wizard")
        assert meta is not None
        assert meta["wizard_class"] is ConcreteFlowWizard

    def test_subclass_redeclaring_abstract_skips_registration(self) -> None:
        """A subclass with its own Meta.abstract = True stays unregistered."""

        class ReabstractedWizard(DemoWizard):
            class Meta:
                abstract = True
                steps: ClassVar = [("identity", IdentityStep)]

        assert (
            form_action_manager.default_backend.get_meta("reabstracted_wizard") is None
        )

    def test_empty_steps_records_wizard_without_steps(self) -> None:
        """A wizard with empty Meta.steps is flagged for the E050 check."""

        class SteplessWizard(FormWizard):
            class Meta:
                steps: ClassVar = []

        assert (
            SteplessWizard.__qualname__ in registration_diagnostics.wizard_without_steps
        )

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

    def test_diagnostics_clear_empties_wizard_list(self) -> None:
        """`registration_diagnostics.clear` drops accumulated registration state."""
        snapshot = registration_diagnostics.snapshot()
        registration_diagnostics.clear()
        assert registration_diagnostics.wizard_without_steps == []
        registration_diagnostics.restore(snapshot)

    def test_virtual_path_skips_registration(self) -> None:
        """A wizard defined in a virtual frame is not registered."""
        with patch("next.forms.base._find_definition_frame", return_value="<stdin>"):

            class VirtualWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert form_action_manager.default_backend.get_meta("virtual_wizard") is None

    def test_empty_path_skips_registration(self) -> None:
        """A wizard whose definition frame is empty is not registered."""
        with patch("next.forms.base._find_definition_frame", return_value=""):

            class EmptyPathWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert form_action_manager.default_backend.get_meta("empty_path_wizard") is None

    def test_framework_file_skips_registration(self) -> None:
        """A wizard defined inside the framework package is not registered."""
        framework_path = str(_FRAMEWORK_ROOT / "forms" / "wizard.py")
        with patch(
            "next.forms.base._find_definition_frame", return_value=framework_path
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

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class OutsideWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

        assert any(
            "OutsideWizard" in name
            for name, _ in registration_diagnostics.outside_base_dir
        )
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
    """`save_step`, `get_all_cleaned_data`, `completed_steps`, `clear_storage`."""

    def test_save_step_then_get_all_cleaned_data_merges(self) -> None:
        """Saved steps merge into a single cleaned-data mapping read from storage."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        wizard.save_step("scope", {"scope": "ops"})
        assert wizard.get_all_cleaned_data() == {"name": "Ada", "scope": "ops"}

    def test_get_cleaned_data_for_step_returns_step_data(self) -> None:
        """`get_cleaned_data_for_step` returns one step's stored mapping."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        wizard.save_step("scope", {"scope": "ops"})
        assert wizard.get_cleaned_data_for_step("identity") == {"name": "Ada"}
        assert wizard.get_cleaned_data_for_step("scope") == {"scope": "ops"}

    def test_get_cleaned_data_for_step_missing_returns_none(self) -> None:
        """`get_cleaned_data_for_step` returns None for an unstored step."""
        wizard = DemoWizard(_request())
        assert wizard.get_cleaned_data_for_step("identity") is None

    def test_get_cleaned_data_for_step_returns_copy(self) -> None:
        """Mutating the returned mapping never leaks into stored data."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        first = wizard.get_cleaned_data_for_step("identity")
        first["name"] = "mutated"
        assert wizard.get_cleaned_data_for_step("identity") == {"name": "Ada"}

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
        assert wizard.get_all_cleaned_data() == {}
        assert wizard.completed_steps() == []

    def test_wizard_id_and_url_param_defaults(self) -> None:
        """The wizard exposes its derived id and URL parameter."""
        wizard = DemoWizard(_request())
        assert wizard.url_param == "step"
        assert wizard.wizard_id == "demo_wizard"
        assert wizard.storage_id.endswith(":demo_wizard")
        assert wizard.storage_id != wizard.wizard_id


class TestWizardRequestLoadMemo:
    """Per-request load memo and the in-place write-through after save."""

    def test_sibling_instances_share_one_backend_load(self) -> None:
        """Two instances bound to one request pay a single backend load."""
        request = _request()
        first = DemoWizard(request)
        second = DemoWizard(request)
        with patch.object(
            first._backend, "load", wraps=first._backend.load
        ) as load_mock:
            first.completed_steps()
            second.completed_steps()
        assert load_mock.call_count == 1

    def test_save_after_load_skips_the_reload(self) -> None:
        """A save after a load updates the mapping in place without a reload."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.completed_steps()
        with patch.object(
            wizard._backend, "load", wraps=wizard._backend.load
        ) as load_mock:
            wizard.save_step("identity", {"name": "Ada"})
            assert wizard.get_cleaned_data_for_step("identity") == {"name": "Ada"}
        assert load_mock.call_count == 0

    def test_save_from_unloaded_instance_updates_sibling_memo(self) -> None:
        """A save from a fresh instance is visible to an already-loaded sibling."""
        request = _request()
        reader = DemoWizard(request)
        reader.completed_steps()
        writer = DemoWizard(request)
        writer.save_step("identity", {"name": "Ada"})
        assert writer._loaded is reader._loaded
        assert reader.get_cleaned_data_for_step("identity") == {"name": "Ada"}

    def test_clear_storage_drops_request_memo(self) -> None:
        """`clear_storage` evicts the memo so a sibling reads fresh storage."""
        request = _request()
        wizard = DemoWizard(request)
        wizard.save_step("identity", {"name": "Ada"})
        wizard.completed_steps()
        wizard.clear_storage()
        fresh = DemoWizard(request)
        assert fresh.completed_steps() == []


class TestWizardStorageScopeIsolation:
    """Same-named wizards from different modules use distinct storage buckets."""

    def _make_wizard(self, fake_path: str) -> type[FormWizard]:
        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class CheckoutWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

                def done(self, request, cleaned_data) -> HttpResponse:
                    return HttpResponse("ok")

        return CheckoutWizard

    def _two_same_named_wizards(
        self, base_dir: Path
    ) -> tuple[type[FormWizard], type[FormWizard]]:
        paths = []
        for app in ("appone", "apptwo"):
            app_dir = base_dir / app
            app_dir.mkdir()
            (app_dir / "__init__.py").write_text("")
            wizards_file = app_dir / "wizards.py"
            wizards_file.write_text("")
            paths.append(str(wizards_file))
        return self._make_wizard(paths[0]), self._make_wizard(paths[1])

    def test_same_named_wizards_do_not_share_drafts(self, settings, tmp_path) -> None:
        """Equally named wizards in one session keep separate stored steps."""
        settings.BASE_DIR = tmp_path
        first_cls, second_cls = self._two_same_named_wizards(tmp_path)
        request = _request()

        first = first_cls(request)
        first.save_step("identity", {"name": "Ada"})
        second = second_cls(request)

        assert first.wizard_id == second.wizard_id == "checkout_wizard"
        assert first.storage_id != second.storage_id
        assert second.get_all_cleaned_data() == {}
        assert second.completed_steps() == []

    def test_clear_storage_does_not_wipe_other_wizard(self, settings, tmp_path) -> None:
        """`clear_storage` on one wizard leaves the same-named wizard's draft alone."""
        settings.BASE_DIR = tmp_path
        first_cls, second_cls = self._two_same_named_wizards(tmp_path)
        request = _request()

        first_cls(request).save_step("identity", {"name": "Ada"})
        second_cls(request).clear_storage()

        assert first_cls(request).get_all_cleaned_data() == {"name": "Ada"}

    def test_page_scope_wizard_storage_scope_is_page_path(
        self, settings, tmp_path
    ) -> None:
        """A page-scoped wizard keys its storage by the resolved page path."""
        settings.BASE_DIR = tmp_path
        page_dir = tmp_path / "shop"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("")

        wizard_cls = self._make_wizard(str(page_file))
        assert wizard_cls.__dict__["_storage_scope_key"] == str(page_file.resolve())

    def test_unregistered_wizard_falls_back_to_module_scope(self) -> None:
        """An unregistered wizard derives its storage scope from its module."""
        with patch("next.forms.base._find_definition_frame", return_value="<stdin>"):

            class GhostWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", IdentityStep)]

                def done(self, request, cleaned_data) -> HttpResponse:
                    return HttpResponse("ok")

        assert "_storage_scope_key" not in GhostWizard.__dict__
        request = _request()
        wizard = GhostWizard(request)
        assert wizard.storage_id.endswith(":ghost_wizard")
        wizard.save_step("identity", {"name": "Ada"})
        assert GhostWizard(request).get_all_cleaned_data() == {"name": "Ada"}


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


class TestWizardStepCache:
    """Step lookups reuse one `get_steps` evaluation until stored data changes."""

    def test_repeated_lookups_call_get_steps_once(self) -> None:
        """`step_names`, `is_first`, `is_last`, and `step_form_class` share one evaluation."""
        wizard = CountingStepsWizard(_request())
        wizard.step_names()
        wizard.is_first()
        wizard.is_last()
        assert wizard.step_form_class("scope") is ScopeStep
        assert wizard.get_steps_calls == 1

    def test_save_step_invalidates_step_cache(self) -> None:
        """`save_step` drops the cache so conditional steps see fresh data."""
        wizard = CountingStepsWizard(_request())
        wizard.step_names()
        wizard.save_step("identity", {"name": "x"})
        wizard.step_names()
        assert wizard.get_steps_calls == 2

    def test_clear_storage_invalidates_step_cache(self) -> None:
        """`clear_storage` drops the cache alongside the stored data."""
        wizard = CountingStepsWizard(_request())
        wizard.step_names()
        wizard.clear_storage()
        wizard.step_names()
        assert wizard.get_steps_calls == 2

    def test_step_form_class_reuses_cached_mapping(self) -> None:
        """Repeated `step_form_class` lookups reuse one cached mapping."""
        wizard = CountingStepsWizard(_request())
        assert wizard.step_form_class("identity") is IdentityStep
        assert wizard.step_form_class("scope") is ScopeStep
        assert wizard.get_steps_calls == 1


class TestWizardHooks:
    """`get_steps` and `get_form_kwargs` override hooks."""

    def test_get_steps_default_returns_static_steps(self) -> None:
        """The default `get_steps` returns the declared steps."""
        wizard = DemoWizard(_request())
        assert [name for name, _ in wizard.get_steps()] == [
            "identity",
            "scope",
            "approval",
        ]

    def test_get_steps_override_adds_conditional_step(self) -> None:
        """A `get_steps` override can insert a conditional step."""

        class ConditionalWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", IdentityStep), ("scope", ScopeStep)]

            def get_steps(self):
                base = [("identity", IdentityStep), ("scope", ScopeStep)]
                if self.get_all_cleaned_data().get("name") == "needs-detail":
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
        assert wizard.get_form_kwargs("identity") == {}

    def test_get_form_kwargs_override_feeds_current_form(self) -> None:
        """A `get_form_kwargs` override flows into the constructed step form."""

        class PrefixField(Form):
            label = django_forms.CharField(max_length=50)

        class KwargsWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("only", PrefixField)]

            def get_form_kwargs(self, step=None):
                return {"prefix": "wiz"}

            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponse("ok")

        form = KwargsWizard(_request()).current_form()
        assert form.prefix == "wiz"

    def test_current_form_passes_step_to_get_form_kwargs(self) -> None:
        """`current_form` feeds the current step name into `get_form_kwargs`."""

        class StepEchoWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", IdentityStep), ("scope", ScopeStep)]

            seen_steps: ClassVar[list] = []

            def get_form_kwargs(self, step=None):
                type(self).seen_steps.append(step)
                return {}

            def done(self, request, cleaned_data) -> HttpResponse:
                return HttpResponse("ok")

        StepEchoWizard.seen_steps.clear()
        StepEchoWizard(_request(), url_kwargs={"step": "scope"}).current_form()
        assert StepEchoWizard.seen_steps == ["scope"]


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

    def test_save_step_without_session_support_raises(self) -> None:
        """`save_step` on a request without session support fails loudly."""
        backend = CacheFormWizardBackend()
        request = RequestFactory().post("/wizard/identity/")
        with pytest.raises(ImproperlyConfigured, match="requires Django sessions"):
            backend.save_step(request, "wiz", "identity", {"name": "Ada"})

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


class TestSessionFormWizardBackend:
    """`SessionFormWizardBackend`: round-trip, codec, and session requirements."""

    def test_save_load_clear_round_trip(self) -> None:
        """save_step stores per-step data that load returns and clear drops."""
        backend = SessionFormWizardBackend()
        request = _request()
        backend.save_step(request, "wiz", "identity", {"name": "Ada"})
        backend.save_step(request, "wiz", "scope", {"scope": "ops"})
        assert backend.load(request, "wiz") == {
            "identity": {"name": "Ada"},
            "scope": {"scope": "ops"},
        }
        backend.clear(request, "wiz")
        assert backend.load(request, "wiz") == {}

    def test_distinct_wizard_ids_are_isolated(self) -> None:
        """Two wizard ids in one session never see each other's drafts."""
        backend = SessionFormWizardBackend()
        request = _request()
        backend.save_step(request, "first", "identity", {"name": "Ada"})
        assert backend.load(request, "second") == {}

    def test_stored_bucket_is_json_safe(self) -> None:
        """The session bucket holds only JSON-serialisable encoded values."""
        backend = SessionFormWizardBackend()
        request = _request()
        backend.save_step(
            request,
            "wiz",
            "identity",
            {"when": datetime.date(2026, 6, 11), "amount": Decimal("9.50")},
        )
        raw = request.session["_next_wizard:wiz"]
        assert json.dumps(raw)

    def test_scalar_codec_round_trips_tagged_types(self) -> None:
        """date, datetime, time, Decimal, and UUID survive the session codec."""
        backend = SessionFormWizardBackend()
        request = _request()
        token = uuid4()
        data = {
            "day": datetime.date(2026, 6, 11),
            "at": datetime.datetime(2026, 6, 11, 12, 30, tzinfo=datetime.UTC),
            "slot": datetime.time(9, 15),
            "amount": Decimal("9.50"),
            "token": token,
        }
        backend.save_step(request, "wiz", "identity", data)
        loaded = backend.load(request, "wiz")["identity"]
        assert loaded == data
        assert isinstance(loaded["day"], datetime.date)
        assert isinstance(loaded["at"], datetime.datetime)
        assert isinstance(loaded["slot"], datetime.time)
        assert isinstance(loaded["amount"], Decimal)
        assert isinstance(loaded["token"], UUID)

    def test_nested_containers_round_trip(self) -> None:
        """Lists and nested dicts encode recursively, tuples come back as lists."""
        backend = SessionFormWizardBackend()
        request = _request()
        data = {
            "days": [datetime.date(2026, 6, 11), datetime.date(2026, 6, 12)],
            "pair": ("a", "b"),
            "nested": {"amount": Decimal("1.25")},
        }
        backend.save_step(request, "wiz", "identity", data)
        loaded = backend.load(request, "wiz")["identity"]
        assert loaded["days"] == [
            datetime.date(2026, 6, 11),
            datetime.date(2026, 6, 12),
        ]
        assert loaded["pair"] == ["a", "b"]
        assert loaded["nested"] == {"amount": Decimal("1.25")}

    def test_dict_colliding_with_codec_key_round_trips(self) -> None:
        """A user dict carrying the codec tag key is escaped and restored."""
        backend = SessionFormWizardBackend()
        request = _request()
        data = {"payload": {"__next_wizard__": "user-value", "other": 1}}
        backend.save_step(request, "wiz", "identity", data)
        loaded = backend.load(request, "wiz")["identity"]
        assert loaded == data

    @pytest.mark.django_db()
    def test_model_instance_round_trips_by_pk(self) -> None:
        """A model instance is stored as a pk reference and refetched on load."""
        backend = SessionFormWizardBackend()
        request = _request()
        user = User.objects.create(username="ada")
        backend.save_step(request, "wiz", "identity", {"owner": user})
        loaded = backend.load(request, "wiz")["identity"]
        assert isinstance(loaded["owner"], User)
        assert loaded["owner"].pk == user.pk

    @pytest.mark.django_db()
    def test_deleted_model_instance_decodes_to_none(self) -> None:
        """A draft outliving its row loads the missing instance as None."""
        backend = SessionFormWizardBackend()
        request = _request()
        user = User.objects.create(username="ada")
        backend.save_step(request, "wiz", "identity", {"owner": user})
        user.delete()
        loaded = backend.load(request, "wiz")["identity"]
        assert loaded["owner"] is None

    def test_unsaved_model_instance_raises(self) -> None:
        """An instance without a pk cannot be stored as a reference."""
        backend = SessionFormWizardBackend()
        request = _request()
        with pytest.raises(ImproperlyConfigured, match="cannot store User"):
            backend.save_step(request, "wiz", "identity", {"owner": User()})

    def test_unsupported_value_raises_with_backend_hint(self) -> None:
        """A non-JSON value without a codec fails loudly and names the way out."""
        backend = SessionFormWizardBackend()
        request = _request()
        with pytest.raises(ImproperlyConfigured, match="CacheFormWizardBackend"):
            backend.save_step(request, "wiz", "identity", {"blob": object()})

    def test_non_string_dict_key_raises(self) -> None:
        """A mapping with non-string keys cannot survive JSON and fails loudly."""
        backend = SessionFormWizardBackend()
        request = _request()
        with pytest.raises(ImproperlyConfigured, match="cannot store int"):
            backend.save_step(request, "wiz", "identity", {"choices": {1: "a"}})

    def test_save_step_without_session_support_raises(self) -> None:
        """`save_step` on a request without session support fails loudly."""
        backend = SessionFormWizardBackend()
        request = RequestFactory().post("/wizard/identity/")
        with pytest.raises(ImproperlyConfigured, match="requires Django sessions"):
            backend.save_step(request, "wiz", "identity", {"name": "Ada"})

    def test_load_without_session_returns_empty(self) -> None:
        """`load` on a request without session support returns an empty mapping."""
        backend = SessionFormWizardBackend()
        request = RequestFactory().get("/")
        assert backend.load(request, "wiz") == {}

    def test_clear_without_session_is_a_noop(self) -> None:
        """`clear` on a request without session support does nothing."""
        backend = SessionFormWizardBackend()
        request = RequestFactory().get("/")
        backend.clear(request, "wiz")


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
        assert isinstance(first, SessionFormWizardBackend)
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
        with override_settings(NEXT_FRAMEWORK={"FORM_WIZARD_BACKEND": config}):
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
                "FORM_WIZARD_BACKEND": {
                    "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
                    "OPTIONS": {},
                }
            }
        ):
            next_framework_settings.reload()
            assert wizard_backend_manager.get() is not first
