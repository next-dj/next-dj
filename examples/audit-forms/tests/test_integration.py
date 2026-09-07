import importlib.util
import re
from pathlib import Path

import pytest
from access.models import AccessRequest, AuditEntry
from django.core.cache import caches
from django.http import HttpRequest

from next.forms import FormActionNotFoundError
from next.forms.signals import (
    action_dispatched,
    form_access_denied,
    form_validation_failed,
)
from next.testing import SignalRecorder, envelope_of, resolve_action_url


pytestmark = pytest.mark.django_db


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
    spec = importlib.util.spec_from_file_location(
        "audit_step_page_e2e", _STEP_PAGE_PATH
    )
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


def _post_step(next_client, step: str, data: dict[str, str]):
    payload = {**data, "policy_acknowledged": "on"}
    return next_client.post_action(WIZARD_ACTION, payload, origin=f"/request/{step}/")


def _post_step_unacknowledged(next_client, step: str, data: dict[str, str]):
    return next_client.post_action(
        WIZARD_ACTION, dict(data), origin=f"/request/{step}/"
    )


def _post_step_partial(next_client, step: str, data: dict[str, str]):
    payload = {**data, "policy_acknowledged": "on"}
    return next_client.post_action(
        WIZARD_ACTION,
        payload,
        origin=f"/request/{step}/",
        partial=True,
        zones="access-wizard",
    )


def _validate_zone() -> str:
    """Return the synthetic queue key the runtime sends on a blur probe.

    `triggers.ts` queues inline validation on `validate:<uid>` and `wire.ts`
    ships that key in the zone header, so the origin page declares no such
    zone and the server answers with the extract-morph of the form by uid.
    """
    uid = resolve_action_url(WIZARD_ACTION).rstrip("/").rsplit("/", 1)[1]
    return f"validate:{uid}"


def _validate_field(next_client, step: str, field: str, data: dict[str, str]):
    return next_client.post_action(
        WIZARD_ACTION,
        dict(data),
        origin=f"/request/{step}/",
        partial=True,
        zones=_validate_zone(),
        HTTP_X_NEXT_VALIDATE=field,
    )


def _morphed_form(response) -> str:
    """Return the HTML the single morph op of a blur probe carries."""
    ops = envelope_of(response).ops
    assert len(ops) == 1
    return ops[0]["html"]


def _walk_three_steps(next_client) -> None:
    _post_step(next_client, "identity", IDENTITY)
    _post_step(next_client, "scope", SCOPE)
    _post_step(next_client, "approval", APPROVAL)


@pytest.fixture()
def submitted_request(next_client) -> AccessRequest:
    _walk_three_steps(next_client)
    return AccessRequest.objects.get()


def _wizard_form_block(html: str) -> str:
    match = re.search(r"<form[^>]*>.*?</form>", html, flags=re.DOTALL)
    assert match is not None
    return match.group(0)


def _form_action_url(block: str) -> str:
    match = re.search(r'<form action="([^"]+)"', block)
    assert match is not None
    return match.group(1)


_POLICY_INPUT = re.compile(r'<input[^>]*name="policy_acknowledged"[^>]*>')


def _policy_input(html: str) -> str:
    match = _POLICY_INPUT.search(html)
    assert match is not None
    return match.group(0)


def _hidden_fields(block: str) -> dict[str, str]:
    return dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', block)
    )


class TestFullSubmission:
    def test_full_three_step_submit_creates_access_request(self, next_client) -> None:
        with SignalRecorder(action_dispatched) as recorder:
            _walk_three_steps(next_client)

        assert AccessRequest.objects.count() == 1
        ar = AccessRequest.objects.get()
        assert ar.full_name == "Ada Lovelace"
        assert ar.email == "ada@example.com"
        assert ar.team == "Computing"
        assert ar.project_slug == "engine"
        assert ar.reason == "Need read access for analysis."
        assert ar.expires_in_days == 14

        events = recorder.events_for(action_dispatched)
        assert len(events) == 3
        statuses = [event.kwargs["response_status"] for event in events]
        assert statuses == [302, 302, 303]
        for event in events:
            assert event.kwargs["duration_ms"] >= 0

    def test_three_steps_log_both_audit_channels(self, next_client) -> None:
        _walk_three_steps(next_client)

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

    def test_backend_rows_capture_the_active_step(self, next_client) -> None:
        _walk_three_steps(next_client)
        rows = list(
            AuditEntry.objects.filter(
                source=AuditEntry.SOURCE_BACKEND, kind=AuditEntry.KIND_REQUEST_STARTED
            )
            .order_by("created_at")
            .values_list("step", "action_name")
        )
        assert [step for step, _ in rows] == ["identity", "scope", "approval"]
        assert {name for _, name in rows} == {"access_request_wizard"}

    def test_signal_rows_carry_timing_and_status(self, next_client) -> None:
        _walk_three_steps(next_client)
        latest_signal = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL
        ).first()
        assert latest_signal is not None
        assert latest_signal.duration_ms is not None
        assert latest_signal.response_status == 303


class TestValidationFailure:
    def test_invalid_submit_records_signal_validation_row(self, next_client) -> None:
        with SignalRecorder(form_validation_failed) as recorder:
            response = _post_step(next_client, "identity", {**IDENTITY, "email": ""})

        assert response.status_code == 200
        assert 'data-state="errors"' in response.content.decode()
        assert AccessRequest.objects.exists() is False

        rows = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL, kind=AuditEntry.KIND_VALIDATION_FAILED
        )
        assert rows.count() == 1
        row = rows.get()
        assert row.error_count >= 1
        assert "email" in row.field_names

        events = recorder.events_for(form_validation_failed)
        assert len(events) == 1
        assert events[0].kwargs["error_count"] >= 1
        assert "email" in events[0].kwargs["field_names"]

    def test_invalid_step_does_not_advance_storage(self, next_client) -> None:
        _post_step(next_client, "identity", {**IDENTITY, "email": ""})
        response = next_client.get("/request/scope/")
        body = response.content.decode()
        assert 'data-step-section="identity"' in body
        assert 'data-step-section="identity" data-state="saved"' not in body

    def test_invalid_then_fixed_resubmit_advances_to_next_step(
        self, next_client
    ) -> None:
        page = next_client.get("/request/identity/")
        assert page.status_code == 200
        block = _wizard_form_block(page.content.decode())
        ack = {"policy_acknowledged": "on"}
        invalid = next_client.post(
            _form_action_url(block),
            {**_hidden_fields(block), **ack, **IDENTITY, "email": ""},
        )
        assert invalid.status_code == 200
        rerendered = _wizard_form_block(invalid.content.decode())
        refields = _hidden_fields(rerendered)
        assert refields["_next_form_origin"] == "/request/identity/"
        fixed = next_client.post(
            _form_action_url(rerendered), {**refields, **ack, **IDENTITY}
        )
        assert fixed.status_code == 302
        assert fixed["Location"] == "/request/scope/"


class TestAccessDenied:
    def test_unacknowledged_step_post_is_denied_with_403(self, next_client) -> None:
        response = _post_step_unacknowledged(next_client, "identity", IDENTITY)
        assert response.status_code == 403
        assert AccessRequest.objects.exists() is False

    def test_denied_step_records_one_signal_access_row(self, next_client) -> None:
        with SignalRecorder(form_access_denied) as recorder:
            _post_step_unacknowledged(next_client, "identity", IDENTITY)

        rows = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_SIGNAL, kind=AuditEntry.KIND_ACCESS_DENIED
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

    def test_denied_step_writes_no_draft(self, next_client) -> None:
        _post_step_unacknowledged(next_client, "identity", IDENTITY)
        body = next_client.get("/request/scope/").content.decode()
        assert 'data-step-section="identity" data-state="saved"' not in body

    def test_acknowledged_step_post_advances(self, next_client) -> None:
        with SignalRecorder(form_access_denied) as recorder:
            response = _post_step(next_client, "identity", IDENTITY)
        assert response.status_code == 302
        assert response["Location"] == "/request/scope/"
        assert recorder.events_for(form_access_denied) == []

    def test_denial_leaves_no_dispatched_backend_row(self, next_client) -> None:
        _post_step_unacknowledged(next_client, "identity", IDENTITY)
        assert (
            AuditEntry.objects.filter(
                source=AuditEntry.SOURCE_BACKEND, kind=AuditEntry.KIND_DISPATCHED
            ).count()
            == 0
        )


class TestSessionResume:
    def test_team_persists_into_step_two(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        response = next_client.get("/request/scope/")
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
    def test_step_draft_lands_in_the_wizards_cache(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        session_key = next_client.session.session_key
        assert session_key is not None
        bucket = _cached_draft(session_key)
        assert set(bucket) == {"identity"}
        assert bucket["identity"]["team"] == "Computing"
        assert bucket["identity"]["email"] == "ada@example.com"

    def test_drafts_stay_out_of_the_default_cache(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        session_key = next_client.session.session_key
        key = f"next_wizard:{session_key}:{_wizard_storage_id()}"
        assert caches["default"].get(key) is None

    def test_drafts_round_trip_across_steps_through_the_cache(
        self, next_client
    ) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "scope", SCOPE)
        bucket = _cached_draft(next_client.session.session_key)
        assert set(bucket) == {"identity", "scope"}
        assert bucket["scope"]["project_slug"] == "engine"
        assert bucket["identity"]["full_name"] == "Ada Lovelace"

    def test_three_step_flow_completes_and_clears_the_cache(self, next_client) -> None:
        session_key_seen = []
        _post_step(next_client, "identity", IDENTITY)
        session_key_seen.append(next_client.session.session_key)
        _post_step(next_client, "scope", SCOPE)
        response = _post_step(next_client, "approval", APPROVAL)
        assert response.status_code == 303
        assert AccessRequest.objects.count() == 1
        assert _cached_draft(session_key_seen[0]) == {}


class TestSuccessRedirect:
    def test_final_step_without_runtime_redirects_to_per_request_page(
        self, next_client
    ) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "scope", SCOPE)
        response = _post_step(next_client, "approval", APPROVAL)
        ar = AccessRequest.objects.get()
        assert response.status_code == 303
        assert response["Location"] == f"/request/{ar.pk}/audit/?just=1"


class TestNamespacedAction:
    def test_auto_name_resolves(self) -> None:
        url = resolve_action_url(WIZARD_ACTION)
        assert url.startswith("/_next/form/")

    def test_namespaced_name_does_not_resolve(self) -> None:
        with pytest.raises(FormActionNotFoundError):
            resolve_action_url("access:access_request_wizard")


class TestAdminAuditPage:
    def test_admin_full_render_shows_only_the_skeleton(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)

        response = next_client.get("/admin/audit/")
        assert response.status_code == 200
        body = response.content.decode()

        assert 'data-next-zone="audit-table"' in body
        assert 'data-next-lazy="revealed"' in body
        assert "data-audit-skeleton" in body
        assert "data-audit-table" not in body
        assert 'data-source="backend"' not in body

    def test_zone_request_morphs_the_audit_table(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "identity", {**IDENTITY, "email": ""})

        response = next_client.get_zones("/admin/audit/", "audit-table")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["audit-table"]
        html = envelope.html_for_zone("audit-table")
        assert 'data-source="backend"' in html
        assert 'data-source="signal"' in html
        assert 'data-kind="dispatched"' in html
        assert 'data-kind="validation_failed"' in html
        assert "data-audit-table" in html

    def test_admin_filter_narrows_to_one_kind(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "identity", {**IDENTITY, "email": ""})

        response = next_client.get_zones(
            "/admin/audit/?kind=validation_failed", "audit-table"
        )
        html = envelope_of(response).html_for_zone("audit-table")
        assert 'data-kind="validation_failed"' in html
        assert 'data-kind="dispatched"' not in html
        assert 'data-kind="request_started"' not in html
        assert (
            AuditEntry.objects.filter(kind=AuditEntry.KIND_VALIDATION_FAILED).count()
            == 1
        )

    def test_admin_surfaces_access_denied_rows(self, next_client) -> None:
        _post_step_unacknowledged(next_client, "identity", IDENTITY)

        response = next_client.get_zones(
            "/admin/audit/?kind=access_denied", "audit-table"
        )
        html = envelope_of(response).html_for_zone("audit-table")
        assert 'data-kind="access_denied"' in html
        assert "view/denied" in html
        assert 'data-kind="dispatched"' not in html


class TestUnknownUid:
    def test_unknown_uid_skips_audit_and_returns_404(self, next_client) -> None:
        response = next_client.post(
            "/_next/form/deadbeefdeadbeef/", {"_next_form_origin": "/request/identity/"}
        )
        assert response.status_code == 404
        assert AuditEntry.objects.filter(source=AuditEntry.SOURCE_BACKEND).count() == 0


class TestRequestCorrelation:
    def test_backend_dispatched_row_links_to_request_when_created(
        self, submitted_request: AccessRequest
    ) -> None:
        attached = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_BACKEND,
            kind=AuditEntry.KIND_DISPATCHED,
            request_id=submitted_request.pk,
        )
        assert attached.count() == 1

    def test_signal_rows_have_no_request_link(self, next_client) -> None:
        _walk_three_steps(next_client)
        unlinked = AuditEntry.objects.filter(source=AuditEntry.SOURCE_SIGNAL).count()
        linked = (
            AuditEntry.objects.filter(source=AuditEntry.SOURCE_SIGNAL)
            .exclude(request_id=None)
            .count()
        )
        assert unlinked == 3
        assert linked == 0


class TestPerRequestAuditPage:
    def test_renders_only_owned_rows(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        first = submitted_request

        next_client.cookies.clear()
        _walk_three_steps(next_client)
        second = AccessRequest.objects.exclude(pk=first.pk).get()

        response = next_client.get(f"/request/{first.pk}/audit/")
        assert response.status_code == 200
        body = response.content.decode()
        assert f"request #{first.pk}" in body
        assert f"request #{second.pk}" not in body
        assert "data-audit-table" in body

    def test_unknown_request_returns_404(self, next_client) -> None:
        response = next_client.get("/request/9999/audit/")
        assert response.status_code == 404

    def test_just_submitted_banner_appears_only_with_query(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        without = next_client.get(f"/request/{submitted_request.pk}/audit/")
        with_flag = next_client.get(f"/request/{submitted_request.pk}/audit/?just=1")
        assert "data-just-submitted" not in without.content.decode()
        assert "data-just-submitted" in with_flag.content.decode()


class TestStepSection:
    def test_active_step_is_marked_active(self, next_client) -> None:
        response = next_client.get("/request/identity/")
        body = response.content.decode()
        assert 'data-step-section="identity"' in body
        assert 'data-step-section="identity" data-state="active"' in body

    def test_saved_badge_appears_on_completed_step(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        response = next_client.get("/request/scope/")
        body = response.content.decode()
        assert 'data-step-section="identity" data-state="saved"' in body
        assert "data-saved-badge" in body

    def test_invalid_submission_renders_errors_state(self, next_client) -> None:
        response = _post_step(next_client, "identity", {**IDENTITY, "email": ""})
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-step-section="identity"' in body
        assert 'data-state="errors"' in body

    def test_review_step_shows_confirmation_summary(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "scope", SCOPE)
        response = next_client.get("/request/approval/")
        body = response.content.decode()
        assert 'data-step-section="approval" data-state="active"' in body
        assert "Confirm and submit" in body
        assert "Ada Lovelace" in body
        assert "engine" in body


class TestModalWizardFlagship:
    def test_landing_links_open_a_layer_around_the_request_list(
        self, next_client
    ) -> None:
        body = next_client.get("/").content.decode()
        assert 'data-next-layer="access-wizard"' in body
        assert 'data-next-accepted="request-list"' in body
        assert 'href="/request/identity/"' in body
        assert 'data-next-zone="request-list"' in body

    def test_the_topbar_entry_opens_the_same_layer(self, next_client) -> None:
        body = next_client.get("/admin/audit/").content.decode()
        opener = re.search(
            r'<a[^>]*href="/request/identity/"[^>]*>\s*Start request', body
        )
        assert opener is not None
        assert 'data-next-layer="access-wizard"' in opener.group(0)
        assert 'data-next-accepted="request-list"' in opener.group(0)

    def test_landing_request_list_marks_each_row_with_its_key(
        self, next_client
    ) -> None:
        ar = AccessRequest.objects.create(
            full_name="Grace Hopper",
            email="grace@example.com",
            team="Compilers",
            project_slug="compilers",
            reason="docs",
            expires_in_days=3,
        )
        body = next_client.get("/").content.decode()
        assert f'data-next-key="{ar.pk}"' in body

    def test_opening_the_layer_fetches_the_wizard_zone_alone(self, next_client) -> None:
        response = next_client.get_zones("/request/identity/", "access-wizard")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["access-wizard"]
        html = envelope.html_for_zone("access-wizard")
        assert 'data-next-validate="blur"' in html
        assert 'data-step-section="identity" data-state="active"' in html

    def test_invalid_step_morphs_the_wizard_zone_and_leaves_the_layer(
        self, next_client
    ) -> None:
        response = _post_step_partial(
            next_client, "identity", {**IDENTITY, "email": ""}
        )
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"
        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["access-wizard"]
        assert "layer.close" not in envelope.op_verbs()
        meta = envelope.form_meta()
        assert meta is not None
        assert meta["valid"] is False
        assert "email" in meta["errors"]
        assert AccessRequest.objects.exists() is False

    def test_valid_non_final_step_advances_the_zone_without_a_redirect(
        self, next_client
    ) -> None:
        response = _post_step_partial(next_client, "identity", IDENTITY)
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["access-wizard"]
        html = envelope.html_for_zone("access-wizard")
        assert 'data-step-section="scope" data-state="active"' in html

    def test_final_step_closes_the_layer_with_a_result_and_a_toast(
        self, next_client
    ) -> None:
        _post_step_partial(next_client, "identity", IDENTITY)
        _post_step_partial(next_client, "scope", SCOPE)
        response = _post_step_partial(next_client, "approval", APPROVAL)
        assert response.status_code == 200
        ar = AccessRequest.objects.get()
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["layer.close", "toast"]
        close_op = envelope.ops[0]
        assert close_op["result"] == {"id": ar.pk}
        toast = envelope.toasts()[0]
        assert toast["variant"] == "success"
        assert toast["text"] == "Access request submitted"

    def test_accept_re_get_morphs_the_request_list_with_the_new_row(
        self, next_client
    ) -> None:
        _post_step_partial(next_client, "identity", IDENTITY)
        _post_step_partial(next_client, "scope", SCOPE)
        _post_step_partial(next_client, "approval", APPROVAL)
        ar = AccessRequest.objects.get()
        response = next_client.get_zones("/", "request-list")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.zone_targets() == ["request-list"]
        html = envelope.html_for_zone("request-list")
        assert f'data-next-key="{ar.pk}"' in html
        assert "Ada Lovelace" in html

    def test_unacknowledged_partial_step_is_denied_without_an_envelope(
        self, next_client
    ) -> None:
        response = next_client.post_action(
            WIZARD_ACTION,
            dict(IDENTITY),
            origin="/request/identity/",
            partial=True,
            zones="access-wizard",
        )
        assert response.status_code == 403
        assert AccessRequest.objects.exists() is False

    def test_partial_done_still_links_the_backend_row_to_the_request(
        self, next_client
    ) -> None:
        _post_step_partial(next_client, "identity", IDENTITY)
        _post_step_partial(next_client, "scope", SCOPE)
        _post_step_partial(next_client, "approval", APPROVAL)
        ar = AccessRequest.objects.get()
        attached = AuditEntry.objects.filter(
            source=AuditEntry.SOURCE_BACKEND,
            kind=AuditEntry.KIND_DISPATCHED,
            request_id=ar.pk,
        )
        assert attached.count() == 1


class TestBlurValidation:
    """A blur probe surfaces one field's error and binds no data."""

    def test_bad_email_blur_morphs_the_form_with_the_field_error(
        self, next_client
    ) -> None:
        before = AccessRequest.objects.count()
        response = _validate_field(
            next_client, "identity", "email", {**IDENTITY, "email": "not-an-email"}
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == []
        assert envelope.form_targets() == [_validate_zone().split(":")[1]]
        meta = envelope.form_meta()
        assert meta is not None
        assert meta["valid"] is False
        assert list(meta["errors"]) == ["email"]
        assert "Enter a valid email address." in _morphed_form(response)
        assert AccessRequest.objects.count() == before == 0

    def test_blur_probe_writes_no_request_and_emits_no_redirect_ops(
        self, next_client
    ) -> None:
        response = _validate_field(
            next_client, "identity", "email", {**IDENTITY, "email": "broken"}
        )
        envelope = envelope_of(response)
        assert "layer.close" not in envelope.op_verbs()
        assert "visit" not in envelope.op_verbs()
        assert "redirect" not in envelope.op_verbs()
        assert AccessRequest.objects.exists() is False

    def test_blur_probe_isolates_the_named_field(self, next_client) -> None:
        response = _validate_field(
            next_client,
            "identity",
            "email",
            {"full_name": "", "email": "bad", "team": ""},
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert list(meta["errors"]) == ["email"]


class TestAcknowledgementRoundTrip:
    """The acknowledgement is a step field, so every re-render replays what was sent."""

    def test_the_first_render_ticks_the_acknowledgement(self, next_client) -> None:
        body = next_client.get("/request/identity/").content.decode()
        assert "checked" in _policy_input(body)

    def test_a_blur_morph_keeps_an_unticked_acknowledgement_unticked(
        self, next_client
    ) -> None:
        response = _validate_field(next_client, "identity", "email", IDENTITY)
        assert "checked" not in _policy_input(_morphed_form(response))

    def test_a_blur_morph_keeps_a_ticked_acknowledgement_ticked(
        self, next_client
    ) -> None:
        response = _validate_field(
            next_client, "identity", "email", {**IDENTITY, "policy_acknowledged": "on"}
        )
        assert "checked" in _policy_input(_morphed_form(response))

    def test_an_invalid_step_morph_keeps_the_acknowledgement_ticked(
        self, next_client
    ) -> None:
        response = _post_step_partial(
            next_client, "identity", {**IDENTITY, "email": ""}
        )
        html = envelope_of(response).html_for_zone("access-wizard")
        assert "checked" in _policy_input(html)

    def test_the_acknowledgement_stays_out_of_the_audit_payload(
        self, next_client
    ) -> None:
        _post_step(next_client, "identity", IDENTITY)
        row = AuditEntry.objects.get(
            source=AuditEntry.SOURCE_BACKEND, kind=AuditEntry.KIND_REQUEST_STARTED
        )
        assert "policy_acknowledged" not in row.payload
        assert row.payload["email"] == ["ada@example.com"]
