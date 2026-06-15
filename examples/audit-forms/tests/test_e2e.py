import importlib.util
import re
from pathlib import Path

import pytest
from access.models import AccessRequest, AuditEntry
from django.core.cache import caches
from django.http import HttpRequest

from next.forms import FormActionNotFound
from next.forms.signals import (
    action_dispatched,
    form_access_denied,
    form_validation_failed,
)
from next.testing import SignalRecorder, resolve_action_url


WIZARD_ACTION = "access_request_wizard"

_STEP_PAGE_PATH = (
    Path(__file__).resolve().parent.parent
    / "access"
    / "views"
    / "request"
    / "[step]"
    / "page.py"
)


def _load_step_page():
    spec = importlib.util.spec_from_file_location("audit_step_page_e2e", _STEP_PAGE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step_page = _load_step_page()

IDENTITY = {
    "full_name": "Ada Lovelace",
    "email": "ada@example.com",
    "team": "Computing",
}
SCOPE = {
    "project_slug": "engine",
    "reason": "Need read access for analysis.",
    "expires_in_days": "14",
}
APPROVAL: dict[str, str] = {}


def _post_step(client, step: str, data: dict[str, str]):
    payload = {**data, "policy_acknowledged": "on"}
    return client.post_action(WIZARD_ACTION, payload, origin=f"/request/{step}/")


def _post_step_unacknowledged(client, step: str, data: dict[str, str]):
    return client.post_action(WIZARD_ACTION, dict(data), origin=f"/request/{step}/")


def _walk_three_steps(client) -> None:
    _post_step(client, "identity", IDENTITY)
    _post_step(client, "scope", SCOPE)
    _post_step(client, "approval", APPROVAL)


def _wizard_form_block(html: str) -> str:
    match = re.search(r"<form[^>]*>.*?</form>", html, flags=re.DOTALL)
    assert match is not None
    return match.group(0)


def _form_action_url(block: str) -> str:
    match = re.search(r'<form action="([^"]+)"', block)
    assert match is not None
    return match.group(1)


def _hidden_fields(block: str) -> dict[str, str]:
    return dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', block)
    )


class TestFullSubmission:
    def test_full_three_step_submit_creates_access_request(self, client) -> None:
        with SignalRecorder(action_dispatched) as recorder:
            _walk_three_steps(client)

        assert AccessRequest.objects.count() == 1
        ar = AccessRequest.objects.get()
        assert ar.full_name == "Ada Lovelace"
        assert ar.email == "ada@example.com"
        assert ar.team == "Computing"
        assert ar.project_slug == "engine"
        assert ar.reason == "Need read access for analysis."
        assert ar.expires_in_days == 14

        assert len(recorder.events_for(action_dispatched)) == 3
        for event in recorder:
            assert event.kwargs["response_status"] == 302
            assert event.kwargs["duration_ms"] >= 0

    def test_three_steps_log_both_audit_channels(self, client) -> None:
        _walk_three_steps(client)

        backend_dispatched = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_BACKEND, kind=AuditEntry.KIND_DISPATCHED
        )
        backend_started = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_BACKEND, kind=AuditEntry.KIND_REQUEST_STARTED
        )
        signal_dispatched = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL, kind=AuditEntry.KIND_DISPATCHED
        )
        assert backend_dispatched.count() == 3
        assert backend_started.count() == 3
        assert signal_dispatched.count() == 3
        assert (
            AuditEntry.objects.filter(
                source=AuditEntry.SOURCE_SIGNAL, kind=AuditEntry.KIND_VALIDATION_FAILED
            ).count()
            == 0
        )

    def test_backend_rows_capture_the_active_step(self, client) -> None:
        _walk_three_steps(client)
        rows = list(
            AuditEntry.objects.filter(
                source=AuditEntry.SOURCE_BACKEND,
                kind=AuditEntry.KIND_REQUEST_STARTED,
            )
            .order_by("created_at")
            .values_list("step", "action_name")
        )
        assert [step for step, _ in rows] == ["identity", "scope", "approval"]
        assert {name for _, name in rows} == {"access_request_wizard"}

    def test_signal_rows_carry_timing_and_status(self, client) -> None:
        _walk_three_steps(client)
        latest_signal = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL
        ).first()
        assert latest_signal is not None
        assert latest_signal.duration_ms is not None
        assert latest_signal.response_status == 302


class TestValidationFailure:
    def test_invalid_submit_records_signal_validation_row(self, client) -> None:
        with SignalRecorder(form_validation_failed) as recorder:
            response = _post_step(client, "identity", {**IDENTITY, "email": ""})

        assert response.status_code == 200
        assert 'data-state="errors"' in response.content.decode()
        assert AccessRequest.objects.exists() is False

        rows = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL,
            kind=AuditEntry.KIND_VALIDATION_FAILED,
        )
        assert rows.count() == 1
        row = rows.get()
        assert row.error_count >= 1
        assert "email" in row.field_names

        events = recorder.events_for(form_validation_failed)
        assert len(events) == 1
        assert events[0].kwargs["error_count"] >= 1
        assert "email" in events[0].kwargs["field_names"]

    def test_invalid_step_does_not_advance_storage(self, client) -> None:
        _post_step(client, "identity", {**IDENTITY, "email": ""})
        response = client.get("/request/scope/")
        body = response.content.decode()
        assert 'data-step-section="identity"' in body
        assert 'data-step-section="identity" data-state="saved"' not in body

    def test_invalid_then_fixed_resubmit_advances_to_next_step(self, client) -> None:
        page = client.get("/request/identity/")
        assert page.status_code == 200
        block = _wizard_form_block(page.content.decode())
        ack = {"policy_acknowledged": "on"}
        invalid = client.post(
            _form_action_url(block),
            {**_hidden_fields(block), **ack, **IDENTITY, "email": ""},
        )
        assert invalid.status_code == 200
        rerendered = _wizard_form_block(invalid.content.decode())
        refields = _hidden_fields(rerendered)
        assert refields["_next_form_origin"] == "/request/identity/"
        fixed = client.post(
            _form_action_url(rerendered), {**refields, **ack, **IDENTITY}
        )
        assert fixed.status_code == 302
        assert fixed["Location"] == "/request/scope/"


class TestAccessDenied:
    def test_unacknowledged_step_post_is_denied_with_403(self, client) -> None:
        response = _post_step_unacknowledged(client, "identity", IDENTITY)
        assert response.status_code == 403
        assert AccessRequest.objects.exists() is False

    def test_denied_step_records_one_signal_access_row(self, client) -> None:
        with SignalRecorder(form_access_denied) as recorder:
            _post_step_unacknowledged(client, "identity", IDENTITY)

        rows = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL,
            kind=AuditEntry.KIND_ACCESS_DENIED,
        )
        assert rows.count() == 1
        row = rows.get()
        assert row.access_layer == "view"
        assert row.access_reason == "denied"
        assert row.action_name == WIZARD_ACTION

        events = recorder.events_for(form_access_denied)
        assert len(events) == 1
        assert events[0].kwargs["layer"] == "view"
        assert events[0].kwargs["reason"] == "denied"

    def test_denied_step_writes_no_draft(self, client) -> None:
        _post_step_unacknowledged(client, "identity", IDENTITY)
        body = client.get("/request/scope/").content.decode()
        assert 'data-step-section="identity" data-state="saved"' not in body

    def test_acknowledged_step_post_advances(self, client) -> None:
        with SignalRecorder(form_access_denied) as recorder:
            response = _post_step(client, "identity", IDENTITY)
        assert response.status_code == 302
        assert response["Location"] == "/request/scope/"
        assert recorder.events_for(form_access_denied) == []

    def test_denial_leaves_no_dispatched_backend_row(self, client) -> None:
        _post_step_unacknowledged(client, "identity", IDENTITY)
        assert (
            AuditEntry.objects.filter(
                source=AuditEntry.SOURCE_BACKEND,
                kind=AuditEntry.KIND_DISPATCHED,
            ).count()
            == 0
        )


class TestSessionResume:
    def test_team_persists_into_step_two(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        response = client.get("/request/scope/")
        body = response.content.decode()
        assert "Computing" in body
        assert 'data-step-section="identity" data-state="saved"' in body
        assert "data-saved-badge" in body


def _wizard_storage_id() -> str:
    wizard = step_page.AccessRequestWizard(
        request=HttpRequest(),
        url_kwargs={"step": "identity"},
        base_path="/request/identity/",
    )
    return wizard.storage_id


def _cached_draft(session_key: str) -> dict:
    key = f"next_wizard:{session_key}:{_wizard_storage_id()}"
    return caches["wizards"].get(key) or {}


class TestCacheBackedDrafts:
    def test_step_draft_lands_in_the_wizards_cache(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        session_key = client.session.session_key
        assert session_key is not None
        bucket = _cached_draft(session_key)
        assert set(bucket) == {"identity"}
        assert bucket["identity"]["team"] == "Computing"
        assert bucket["identity"]["email"] == "ada@example.com"

    def test_drafts_stay_out_of_the_default_cache(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        session_key = client.session.session_key
        key = f"next_wizard:{session_key}:{_wizard_storage_id()}"
        assert caches["default"].get(key) is None

    def test_drafts_round_trip_across_steps_through_the_cache(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        _post_step(client, "scope", SCOPE)
        bucket = _cached_draft(client.session.session_key)
        assert set(bucket) == {"identity", "scope"}
        assert bucket["scope"]["project_slug"] == "engine"
        assert bucket["identity"]["full_name"] == "Ada Lovelace"

    def test_three_step_flow_completes_and_clears_the_cache(self, client) -> None:
        session_key_seen = []
        _post_step(client, "identity", IDENTITY)
        session_key_seen.append(client.session.session_key)
        _post_step(client, "scope", SCOPE)
        response = _post_step(client, "approval", APPROVAL)
        assert response.status_code == 302
        assert AccessRequest.objects.count() == 1
        assert _cached_draft(session_key_seen[0]) == {}


class TestSuccessRedirect:
    def test_final_step_redirects_to_per_request_page(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        _post_step(client, "scope", SCOPE)
        response = _post_step(client, "approval", APPROVAL)
        ar = AccessRequest.objects.get()
        assert response.status_code == 302
        assert response["Location"] == f"/request/{ar.pk}/audit/?just=1"


class TestNamespacedAction:
    def test_auto_name_resolves(self) -> None:
        url = resolve_action_url(WIZARD_ACTION)
        assert url.startswith("/_next/form/")

    def test_namespaced_name_does_not_resolve(self) -> None:
        with pytest.raises(FormActionNotFound):
            resolve_action_url("access:access_request_wizard")


class TestAdminAuditPage:
    def test_admin_lists_both_sources(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        _post_step(client, "identity", {**IDENTITY, "email": ""})

        response = client.get("/admin/audit/")
        assert response.status_code == 200
        body = response.content.decode()

        assert 'data-source="backend"' in body
        assert 'data-source="signal"' in body
        assert 'data-kind="dispatched"' in body
        assert 'data-kind="validation_failed"' in body
        assert "data-audit-table" in body

    def test_admin_filter_narrows_to_one_kind(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        _post_step(client, "identity", {**IDENTITY, "email": ""})

        response = client.get("/admin/audit/?kind=validation_failed")
        body = response.content.decode()
        assert 'data-kind="validation_failed"' in body
        assert 'data-kind="dispatched"' not in body
        assert 'data-kind="request_started"' not in body
        assert (
            AuditEntry.objects.filter(kind=AuditEntry.KIND_VALIDATION_FAILED).count()
            == 1
        )

    def test_admin_surfaces_access_denied_rows(self, client) -> None:
        _post_step_unacknowledged(client, "identity", IDENTITY)

        response = client.get("/admin/audit/?kind=access_denied")
        body = response.content.decode()
        assert 'data-kind="access_denied"' in body
        assert "view/denied" in body
        assert 'data-kind="dispatched"' not in body


class TestUnknownUid:
    def test_unknown_uid_skips_audit_and_returns_404(self, client) -> None:
        response = client.post(
            "/_next/form/deadbeefdeadbeef/", {"_next_form_origin": "/request/identity/"}
        )
        assert response.status_code == 404
        assert AuditEntry.objects.filter(source=AuditEntry.SOURCE_BACKEND).count() == 0


class TestRequestCorrelation:
    def test_backend_dispatched_row_links_to_request_when_created(self, client) -> None:
        _walk_three_steps(client)
        ar = AccessRequest.objects.get()
        attached = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_BACKEND,
            kind=AuditEntry.KIND_DISPATCHED,
            request_id=ar.pk,
        )
        assert attached.count() == 1

    def test_signal_rows_have_no_request_link(self, client) -> None:
        _walk_three_steps(client)
        unlinked = AuditEntry.objects.filter(source=AuditEntry.SOURCE_SIGNAL).count()
        linked = (
            AuditEntry.objects.filter(source=AuditEntry.SOURCE_SIGNAL)
            .exclude(request_id=None)
            .count()
        )
        assert unlinked == 3
        assert linked == 0


class TestPerRequestAuditPage:
    def test_renders_only_owned_rows(self, client) -> None:
        _walk_three_steps(client)
        first = AccessRequest.objects.get()

        client.cookies.clear()
        _walk_three_steps(client)
        second = AccessRequest.objects.exclude(pk=first.pk).get()

        response = client.get(f"/request/{first.pk}/audit/")
        assert response.status_code == 200
        body = response.content.decode()
        assert f"request #{first.pk}" in body
        assert f"request #{second.pk}" not in body
        assert "data-audit-table" in body

    def test_unknown_request_returns_404(self, client) -> None:
        response = client.get("/request/9999/audit/")
        assert response.status_code == 404

    def test_just_submitted_banner_appears_only_with_query(self, client) -> None:
        _walk_three_steps(client)
        ar = AccessRequest.objects.get()

        without = client.get(f"/request/{ar.pk}/audit/")
        with_flag = client.get(f"/request/{ar.pk}/audit/?just=1")
        assert "data-just-submitted" not in without.content.decode()
        assert "data-just-submitted" in with_flag.content.decode()


class TestStepSection:
    def test_active_step_is_marked_active(self, client) -> None:
        response = client.get("/request/identity/")
        body = response.content.decode()
        assert 'data-step-section="identity"' in body
        assert 'data-step-section="identity" data-state="active"' in body

    def test_saved_badge_appears_on_completed_step(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        response = client.get("/request/scope/")
        body = response.content.decode()
        assert 'data-step-section="identity" data-state="saved"' in body
        assert "data-saved-badge" in body

    def test_invalid_submission_renders_errors_state(self, client) -> None:
        response = _post_step(client, "identity", {**IDENTITY, "email": ""})
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-step-section="identity"' in body
        assert 'data-state="errors"' in body

    def test_review_step_shows_confirmation_summary(self, client) -> None:
        _post_step(client, "identity", IDENTITY)
        _post_step(client, "scope", SCOPE)
        response = client.get("/request/approval/")
        body = response.content.decode()
        assert 'data-step-section="approval" data-state="active"' in body
        assert "Confirm and submit" in body
        assert "Ada Lovelace" in body
        assert "engine" in body
