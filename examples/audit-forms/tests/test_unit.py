from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from access.models import AccessRequest, AuditEntry
from django.core.exceptions import ValidationError
from django.http import HttpRequest


if TYPE_CHECKING:
    from types import ModuleType


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
            action_name="access:request_step",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
        )
        entry.created_at = datetime(2026, 4, 25, 12, 30, 0, tzinfo=UTC)
        rendered = str(entry)
        assert "backend/dispatched" in rendered
        assert "access:request_step" in rendered
        assert "2026-04-25 12:30:00" in rendered


class TestProgressBarSteps:
    """`progress_bar` synthesises step status from the URL kwarg and the session."""

    def test_active_step_is_current(self) -> None:
        request = HttpRequest()
        request.session = {}  # type: ignore[assignment]
        steps = _progress._steps(request, step="justification")
        statuses = {entry["key"]: entry["status"] for entry in steps}
        assert statuses["applicant"] == "pending"
        assert statuses["justification"] == "current"
        assert statuses["review"] == "pending"

    def test_filled_session_marks_step_as_saved(self) -> None:
        request = HttpRequest()
        request.session = {  # type: ignore[assignment]
            "access_request": {
                "full_name": "Ada",
                "email": "ada@example.com",
                "team": "Computing",
            },
        }
        steps = _progress._steps(request, step="justification")
        statuses = {entry["key"]: entry["status"] for entry in steps}
        assert statuses["applicant"] == "saved"

    def test_label_helpers_return_canonical_values(self) -> None:
        assert _progress._step_label(step="justification") == "Justification"
        assert _progress._step_index(step="review") == 3
        assert _progress._step_total() == 3


class TestAuditRowHelpers:
    """`_payload_keys` and `_summary` derive admin row fields from `AuditEntry`."""

    def test_payload_keys_returns_dict_keys_excluding_redirect(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_REQUEST_STARTED,
            source=AuditEntry.SOURCE_BACKEND,
            payload={"redirect": "/", "step": "applicant", "email": "a@b"},
        )
        assert sorted(_audit_row._payload_keys(entry)) == ["email", "step"]

    def test_payload_keys_returns_empty_for_non_dict_payload(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
        )
        entry.payload = None  # type: ignore[assignment]
        assert _audit_row._payload_keys(entry) == []

    def test_summary_falls_back_to_dash_when_no_metrics(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
            payload={},
        )
        assert _audit_row._summary(entry) == "—"

    def test_summary_pluralisation_matches_error_count(self) -> None:
        entry = AuditEntry(
            action_name="x",
            kind=AuditEntry.KIND_VALIDATION_FAILED,
            source=AuditEntry.SOURCE_SIGNAL,
            error_count=1,
            field_names=["email"],
        )
        rendered = _audit_row._summary(entry)
        assert "1 error" in rendered
        assert "errors" not in rendered
        assert "email" in rendered


class TestStepFallbacks:
    """Invalid step values normalise to the first step rather than crashing."""

    def test_current_step_normalises_invalid_value(self) -> None:
        assert _step_page.current_step("nope") == _step_page.STEP_ORDER[0]

    def test_progress_bar_normalises_invalid_step(self) -> None:
        request = HttpRequest()
        request.session = {}  # type: ignore[assignment]
        steps = _progress._steps(request, step="nope")
        statuses = [entry["status"] for entry in steps]
        assert statuses[0] == "current"
        assert _progress._step_index(step="nope") == 1


class TestRequestStepFormDerive:
    """`RequestStepForm._derive_step` and `clean_step` enforce the canonical order."""

    def test_unbound_form_with_invalid_initial_falls_back(self) -> None:
        form = _step_page.RequestStepForm(initial={"step": "ghost"})
        assert form.active_step == _step_page.STEP_ORDER[0]

    def test_bound_form_with_invalid_step_raises_validation_error(self) -> None:
        form = _step_page.RequestStepForm(data={"step": "ghost"})
        assert not form.is_valid()
        assert "step" in form.errors

    def test_clean_step_rejects_unknown_value_directly(self) -> None:
        form = _step_page.RequestStepForm(data={"step": _step_page.STEP_ORDER[0]})
        form.cleaned_data = {"step": "ghost"}
        with pytest.raises(ValidationError, match="Unknown step"):
            form.clean_step()

    def test_get_initial_normalises_invalid_step_kwarg(self) -> None:
        request = HttpRequest()
        request.session = {}  # type: ignore[assignment]
        initial = _step_page.RequestStepForm.get_initial(request, step="ghost")
        assert initial["step"] == _step_page.STEP_ORDER[0]


class TestStepSectionRenderPaths:
    """`step_section.render` covers the review, errors, and truncation branches."""

    def test_review_step_renders_summary(self) -> None:
        request = HttpRequest()
        request.session = {  # type: ignore[assignment]
            "access_request": {
                "full_name": "Ada",
                "email": "ada@example.com",
                "team": "Computing",
                "project_slug": "engine",
                "reason": "ok",
                "expires_in_days": 7,
            },
        }
        form = _step_page.RequestStepForm(initial={"step": "review"})
        rendered = _step_section.render(
            form, request, step="review", current_step="review"
        )
        assert 'data-step-section="review"' in rendered
        assert "Confirm and submit" in rendered
        assert "Computing" in rendered

    def test_invalid_active_step_reports_errors_state(self) -> None:
        request = HttpRequest()
        request.session = {}  # type: ignore[assignment]
        form = _step_page.RequestStepForm(
            data={"step": "applicant", "full_name": "", "email": "", "team": ""},
        )
        form.is_valid()  # populate form.errors
        rendered = _step_section.render(
            form,
            request,
            step="applicant",
            current_step="applicant",
        )
        assert 'data-state="errors"' in rendered
        assert "border-rose-300" in rendered

    def test_long_saved_value_is_truncated(self) -> None:
        long_reason = "x" * 200
        request = HttpRequest()
        request.session = {  # type: ignore[assignment]
            "access_request": {
                "project_slug": "engine",
                "reason": long_reason,
                "expires_in_days": 7,
            },
        }
        form = _step_page.RequestStepForm(initial={"step": "applicant"})
        rendered = _step_section.render(
            form,
            request,
            step="justification",
            current_step="applicant",
        )
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
            action_name="access:request_step",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
        )
        response = client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "Grace Hopper" in body
        assert "compilers" in body
        assert "access:request_step" in body
