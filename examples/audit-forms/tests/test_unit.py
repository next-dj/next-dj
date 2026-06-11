import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from access.backends import _safe_form_payload, _step_from_origin
from access.models import AccessRequest, AuditEntry
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpRequest, QueryDict


EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
VIEWS_ROOT = EXAMPLE_ROOT / "access" / "views"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_step_page = _load(VIEWS_ROOT / "request" / "[step]" / "page.py", "audit_step_page")
_progress = _load(
    VIEWS_ROOT / "request" / "[step]" / "_blocks" / "progress_bar" / "component.py",
    "audit_progress_bar",
)
_audit_row = _load(
    VIEWS_ROOT / "_blocks" / "audit_row" / "component.py",
    "audit_audit_row",
)
_step_section = _load(
    VIEWS_ROOT / "request" / "[step]" / "_blocks" / "step_section" / "component.py",
    "audit_step_section",
)


def _wizard(step: str, stored: dict[str, dict[str, object]] | None = None):
    request = HttpRequest()
    request.session = SessionStore()
    wizard = _step_page.AccessRequestWizard(
        request=request,
        url_kwargs={"step": step},
        base_path=f"/request/{step}/",
    )
    for name, data in (stored or {}).items():
        wizard.save_step(name, data)
    return wizard


class TestSafeFormPayload:
    """`_safe_form_payload` keeps real fields and drops framework-internal keys."""

    def test_strips_framework_keys_and_keeps_fields(self) -> None:
        """The persisted payload omits the csrf token and the origin field."""
        request = HttpRequest()
        request.method = "POST"
        request.POST = QueryDict(
            "csrfmiddlewaretoken=tok"
            "&_next_form_origin=/request/identity/"
            "&email=ada@example.com"
            "&full_name=Ada"
        )
        payload = _safe_form_payload(request)
        assert payload == {"email": ["ada@example.com"], "full_name": ["Ada"]}


class TestStepFromOrigin:
    """`_step_from_origin` recovers the wizard step from the origin URL."""

    @staticmethod
    def _request(origin: str | None) -> HttpRequest:
        request = HttpRequest()
        request.method = "POST"
        query = f"_next_form_origin={origin}" if origin is not None else ""
        request.POST = QueryDict(query)
        return request

    def test_resolves_step_kwarg_from_origin_path(self) -> None:
        assert _step_from_origin(self._request("/request/scope/")) == "scope"

    def test_ignores_query_string_on_the_origin(self) -> None:
        assert _step_from_origin(self._request("/request/identity/?just=1")) == (
            "identity"
        )

    def test_missing_origin_yields_empty_step(self) -> None:
        assert _step_from_origin(self._request(None)) == ""

    def test_relative_origin_yields_empty_step(self) -> None:
        assert _step_from_origin(self._request("request/identity/")) == ""

    def test_unroutable_origin_yields_empty_step(self) -> None:
        assert _step_from_origin(self._request("/no/such/page/")) == ""

    def test_origin_without_step_kwarg_yields_empty_step(self) -> None:
        assert _step_from_origin(self._request("/")) == ""


class TestModelStr:
    """`__str__` carries the friendly identifiers used in admin and shell."""

    def test_access_request_str_lists_identity_and_status(self) -> None:
        req = AccessRequest(
            full_name="Ada Lovelace",
            email="ada@example.com",
            team="Computing",
            project_slug="engine",
            reason="reads",
            expires_in_days=7,
            status="pending",
        )
        rendered = str(req)
        assert "Ada Lovelace" in rendered
        assert "ada@example.com" in rendered
        assert "engine" in rendered
        assert "pending" in rendered

    def test_audit_entry_str_carries_source_and_kind(self) -> None:
        entry = AuditEntry(
            action_name="access_request_wizard",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
        )
        entry.created_at = datetime(2026, 4, 25, 12, 30, 0, tzinfo=UTC)
        rendered = str(entry)
        assert "backend/dispatched" in rendered
        assert "access_request_wizard" in rendered
        assert "2026-04-25 12:30:00" in rendered


class TestWizardSteps:
    """The three wizard steps map onto disjoint slices of the model fields."""

    def test_identity_step_owns_the_applicant_fields(self) -> None:
        assert list(_step_page.IdentityStep.base_fields) == [
            "full_name",
            "email",
            "team",
        ]

    def test_scope_step_owns_the_request_fields(self) -> None:
        assert list(_step_page.ScopeStep.base_fields) == [
            "project_slug",
            "reason",
            "expires_in_days",
        ]

    def test_approval_step_has_no_fields(self) -> None:
        assert list(_step_page.ApprovalStep.base_fields) == []

    def test_wizard_declares_three_ordered_steps(self) -> None:
        names = [name for name, _ in _step_page.AccessRequestWizard.Meta.steps]
        assert names == ["identity", "scope", "approval"]


class TestProgressBarSteps:
    """`progress_bar` synthesises step status from the wizard state."""

    def test_active_step_is_current(self) -> None:
        steps = _progress.steps(_wizard("scope"))
        statuses = {entry["key"]: entry["status"] for entry in steps}
        assert statuses["identity"] == "pending"
        assert statuses["scope"] == "current"
        assert statuses["approval"] == "pending"

    def test_stored_step_is_marked_saved(self) -> None:
        wizard = _wizard("scope", {"identity": {"team": "Computing"}})
        steps = _progress.steps(wizard)
        statuses = {entry["key"]: entry["status"] for entry in steps}
        assert statuses["identity"] == "saved"

    def test_label_helpers_return_canonical_values(self) -> None:
        assert _progress.step_label(_wizard("scope")) == "Scope"
        assert _progress.step_index(_wizard("approval")) == 3
        assert _progress.step_total(_wizard("identity")) == 3


class TestAuditRowHelpers:
    """`payload_keys` and `summary` derive admin row fields from `AuditEntry`."""

    def test_payload_keys_returns_dict_keys_excluding_redirect(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_REQUEST_STARTED,
            source=AuditEntry.SOURCE_BACKEND,
            payload={"redirect": "/", "team": "Computing", "email": "a@b"},
        )
        assert sorted(_audit_row.payload_keys(entry)) == ["email", "team"]

    def test_payload_keys_returns_empty_for_non_dict_payload(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
        )
        entry.payload = None  # type: ignore[assignment]
        assert _audit_row.payload_keys(entry) == []

    def test_summary_falls_back_to_dash_when_no_metrics(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
            payload={},
        )
        assert _audit_row.summary(entry) == "—"

    def test_summary_pluralisation_matches_error_count(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_VALIDATION_FAILED,
            source=AuditEntry.SOURCE_SIGNAL,
            error_count=1,
            field_names=["email"],
        )
        rendered = _audit_row.summary(entry)
        assert "1 error" in rendered
        assert "errors" not in rendered
        assert "email" in rendered


class TestStepFormValidation:
    """The model-backed steps validate only their own fields."""

    def test_identity_step_requires_email(self) -> None:
        form = _step_page.IdentityStep(
            data={"full_name": "Ada", "email": "", "team": "Computing"},
        )
        assert not form.is_valid()
        assert "email" in form.errors

    def test_scope_step_accepts_valid_payload(self) -> None:
        form = _step_page.ScopeStep(
            data={
                "project_slug": "engine",
                "reason": "reads",
                "expires_in_days": "7",
            },
        )
        assert form.is_valid()
        assert form.cleaned_data["project_slug"] == "engine"

    def test_approval_step_is_always_valid(self) -> None:
        form = _step_page.ApprovalStep(data={})
        assert form.is_valid()
        assert form.cleaned_data == {}


class TestStepSectionRenderPaths:
    """`step_section.render` covers the review, saved, and errors branches."""

    def test_review_step_renders_summary(self) -> None:
        wizard = _wizard(
            "approval",
            {
                "identity": {
                    "full_name": "Ada",
                    "email": "ada@example.com",
                    "team": "Computing",
                },
                "scope": {
                    "project_slug": "engine",
                    "reason": "ok",
                    "expires_in_days": 7,
                },
            },
        )
        rendered = _step_section.render(wizard.current_form(), wizard)
        assert 'data-step-section="approval"' in rendered
        assert "Confirm and submit" in rendered
        assert "Computing" in rendered

    def test_invalid_active_step_reports_errors_state(self) -> None:
        wizard = _wizard("identity")
        form = _step_page.IdentityStep(
            data={"full_name": "", "email": "", "team": ""},
        )
        form.is_valid()
        rendered = _step_section.render(form, wizard)
        assert 'data-state="errors"' in rendered
        assert "border-rose-300" in rendered

    def test_long_saved_value_is_truncated(self) -> None:
        long_reason = "x" * 200
        wizard = _wizard(
            "identity",
            {
                "scope": {
                    "project_slug": "engine",
                    "reason": long_reason,
                    "expires_in_days": 7,
                },
            },
        )
        rendered = _step_section.render(wizard.current_form(), wizard)
        assert 'data-step-section="scope" data-state="saved"' in rendered
        assert "..." in rendered
        assert long_reason not in rendered


class TestLandingPage:
    """The landing page exposes the most recent requests and audit rows."""

    def test_landing_lists_recent_request_and_audit_summaries(self, client) -> None:
        AccessRequest.objects.create(
            full_name="Grace Hopper",
            email="grace@example.com",
            team="Compilers",
            project_slug="compilers",
            reason="docs",
            expires_in_days=3,
        )
        AuditEntry.objects.create(
            action_name="access_request_wizard",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
        )
        response = client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "Grace Hopper" in body
        assert "compilers" in body
        assert "access_request_wizard" in body
