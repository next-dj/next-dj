import importlib.util
import re
from html import unescape
from io import StringIO
from pathlib import Path

import pytest
from access.demo import REQUESTERS
from access.models import (
    AccessRequest,
    AccessRequestQuerySet,
    AuditEntry,
    AuditEntryQuerySet,
)
from django.core.cache import caches
from django.core.checks import run_checks
from django.core.management import call_command
from django.db import OperationalError
from django.http import HttpRequest

from next.forms import FormActionNotFoundError
from next.forms.signals import (
    action_dispatched,
    form_access_denied,
    form_validation_failed,
)
from next.testing import (
    SignalRecorder,
    assert_metadata,
    boundary_errors,
    envelope_of,
    find_anchor,
    find_form,
    hidden_fields,
    resolve_action_url,
)


pytestmark = pytest.mark.django_db(transaction=True)


WIZARD_ACTION = "access_request_wizard"

_STEP_PAGE_PATH = (
    Path(__file__).resolve().parent.parent
    / "access"
    / "pages"
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
        WIZARD_ACTION, payload, origin=f"/request/{step}/", partial="access-wizard"
    )


def _validate_field(next_client, step: str, field: str, data: dict[str, str]):
    return next_client.post_action(
        WIZARD_ACTION,
        dict(data),
        origin=f"/request/{step}/",
        partial="access-wizard",
        HTTP_X_NEXT_VALIDATE=field,
    )


def _walk_three_steps(next_client) -> None:
    _post_step(next_client, "identity", IDENTITY)
    _post_step(next_client, "scope", SCOPE)
    _post_step(next_client, "approval", APPROVAL)


@pytest.fixture()
def submitted_request(next_client) -> AccessRequest:
    _walk_three_steps(next_client)
    return AccessRequest.objects.get()


LOG_URL = "/admin/audit/"
LOG_ZONES = "audit-rows,audit-rows-more,audit-rows-less,audit-totals"
OLDER = "Show older rows"
NEWER = "Show newer rows"

_ROW_KEY = re.compile(r'<tr data-audit-row data-next-key="(\d+)"')
_HREF = re.compile(r'href="([^"]*)"')
_LOG_ZONE = r'<div data-next-zone="audit-rows-{}"[^>]*>([\s\S]*?)</div>'


def _row_keys(html: str) -> list[int]:
    return [int(key) for key in _ROW_KEY.findall(html)]


def _log_order(**filters: str) -> list[int]:
    rows = AuditEntry.objects.filter(**filters).order_by("-created_at", "-pk")
    return list(rows.values_list("pk", flat=True))


def _sentinel(body: str, suffix: str) -> str:
    match = re.search(_LOG_ZONE.format(suffix), body)
    assert match is not None
    return match.group(1)


def _link(html: str, label: str) -> str:
    match = _HREF.search(find_anchor(html, text=label))
    assert match is not None
    return unescape(match.group(1))


def _older(next_client, href: str):
    return envelope_of(
        next_client.get_zones(
            f"{LOG_URL}{href}", "audit-rows,audit-rows-more", HTTP_X_NEXT_MERGE="append"
        )
    )


def _newer(next_client, href: str):
    return envelope_of(
        next_client.get_zones(
            f"{LOG_URL}{href}",
            "audit-rows,audit-rows-less",
            HTTP_X_NEXT_MERGE="prepend",
        )
    )


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
            {**hidden_fields(block), **ack, **IDENTITY, "email": ""},
        )
        assert invalid.status_code == 200
        rerendered = _wizard_form_block(invalid.content.decode())
        refields = hidden_fields(rerendered)
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
        assert "✓ saved" in body


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
    def test_the_first_render_lists_the_rows_of_both_channels(
        self, next_client
    ) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "identity", {**IDENTITY, "email": ""})

        response = next_client.get(LOG_URL)
        assert response.status_code == 200
        body = response.content.decode()

        assert '<tbody data-next-zone="audit-rows"' in body
        assert "data-audit-table" in body
        assert 'data-source="backend"' in body
        assert 'data-source="signal"' in body
        assert 'data-kind="dispatched"' in body
        assert 'data-kind="validation_failed"' in body
        assert _row_keys(body) == _log_order()
        assert "{{" not in body

    def test_an_empty_log_says_so(self, next_client) -> None:
        body = next_client.get(LOG_URL).content.decode()
        assert "No audit rows yet" in _sentinel(body, "more")
        assert _row_keys(body) == []

    def test_a_short_log_ends_under_its_rows(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)

        more = _sentinel(next_client.get(LOG_URL).content.decode(), "more")
        assert "End of the log." in more
        assert "<a" not in more

    def test_kind_filter_reloads_every_zone_it_narrows(self, next_client) -> None:
        body = next_client.get(f"{LOG_URL}?kind=dispatched").content.decode()
        form = find_form(body, contains='id="kind-filter"')
        assert 'method="get"' in form
        assert f'data-next-target="{LOG_ZONES}"' in form
        assert 'data-next-trigger="change"' in form
        assert "onchange" not in form
        assert "<noscript>" in form
        assert '<option value="dispatched" selected>' in form
        for zone in LOG_ZONES.split(","):
            assert f'data-next-zone="{zone}"' in body

    def test_the_filter_request_morphs_the_rows_and_resets_the_totals(
        self, next_client
    ) -> None:
        _post_step(next_client, "identity", IDENTITY)
        _post_step(next_client, "identity", {**IDENTITY, "email": ""})

        response = next_client.get_zones(f"{LOG_URL}?kind=validation_failed", LOG_ZONES)
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"] * 4
        assert envelope.zone_targets() == LOG_ZONES.split(",")
        rows = envelope.html_for_zone("audit-rows")
        assert _row_keys(rows) == _log_order(kind=AuditEntry.KIND_VALIDATION_FAILED)
        assert 'data-kind="dispatched"' not in rows
        totals = envelope.html_for_zone("audit-totals")
        assert 'data-next-defer="visible"' in totals
        assert "data-audit-skeleton" in totals

    def test_an_unknown_kind_lists_every_row(self, next_client) -> None:
        _post_step(next_client, "identity", IDENTITY)

        body = next_client.get(f"{LOG_URL}?kind=bogus").content.decode()
        assert _row_keys(body) == _log_order()

    def test_admin_surfaces_access_denied_rows(self, next_client) -> None:
        _post_step_unacknowledged(next_client, "identity", IDENTITY)

        body = next_client.get(f"{LOG_URL}?kind=access_denied").content.decode()
        assert 'data-kind="access_denied"' in body
        assert "view/denied" in body
        assert 'data-kind="dispatched"' not in body


class TestAuditTotals:
    """The per-channel counts load on their own request, behind a skeleton."""

    def test_the_full_render_holds_the_skeleton_and_counts_nothing(
        self, next_client
    ) -> None:
        _post_step(next_client, "identity", IDENTITY)

        body = next_client.get(LOG_URL).content.decode()
        assert 'data-next-zone="audit-totals"' in body
        assert 'data-next-defer="visible"' in body
        assert "data-audit-skeleton" in body
        assert "data-audit-totals" not in body

    def test_the_totals_arrive_on_their_own_request(
        self, next_client, demo_data
    ) -> None:
        totals = next_client.get_boundary(LOG_URL, "audit_totals", kind="")
        assert totals.state == "ready"
        backend = AuditEntry.objects.filter(source=AuditEntry.SOURCE_BACKEND).count()
        assert "data-audit-totals" in totals.html
        assert f">{backend}</p>" in totals.html
        assert f">{demo_data - backend}</p>" in totals.html

    def test_the_kind_prop_narrows_the_totals(self, next_client, demo_data) -> None:
        totals = next_client.get_boundary(
            f"{LOG_URL}?kind=access_denied", "audit_totals", kind="access_denied"
        )
        html = totals.html
        denied = AuditEntry.objects.filter(kind=AuditEntry.KIND_ACCESS_DENIED).count()
        assert denied
        assert ">0</p>" in html
        assert f">{denied}</p>" in html

    def test_a_failed_count_offers_a_retry(self, next_client, monkeypatch) -> None:
        def locked(self, kind: str) -> AuditEntryQuerySet:
            msg = "database is locked"
            raise OperationalError(msg)

        monkeypatch.setattr(AuditEntryQuerySet, "of_kind", locked)
        with boundary_errors("render"):
            totals = next_client.get_boundary(LOG_URL, "audit_totals", kind="")
        assert totals.state == "error"
        assert "The totals could not be loaded." in totals.html
        assert "data-next-retry" in totals.html

    def test_a_visitor_without_the_runtime_gets_the_totals_inline(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get(f"{LOG_URL}?_nojs=1").content.decode()
        assert "data-audit-totals" in body
        assert "data-audit-skeleton" not in body


class TestAuditLogWindows:
    """The log pages by a cursor over `(-created_at, -pk)`, one batch per request."""

    def test_the_first_window_holds_the_newest_batch(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get(LOG_URL).content.decode()
        assert _row_keys(body) == _log_order()[:20]
        more = _sentinel(body, "more")
        assert 'data-next-param="cursor"' in more
        assert 'data-next-prune="60"' in more
        assert 'data-next-url="replace"' in more
        assert "data-next-back=" in more
        assert _link(more, OLDER).startswith("?cursor=")
        less = _sentinel(body, "less")
        assert less.count("<a") == 1
        assert " hidden>" in less

    def test_the_next_token_appends_the_following_batch(
        self, next_client, demo_data
    ) -> None:
        first = next_client.get(LOG_URL).content.decode()
        envelope = _older(next_client, _link(first, OLDER))

        assert envelope.op_verbs() == ["append", "append"]
        assert envelope.zone_targets() == ["audit-rows", "audit-rows-more"]
        assert _row_keys(envelope.html_for_zone("audit-rows")) == _log_order()[20:40]
        following = _link(envelope.html_for_zone("audit-rows-more"), OLDER)
        assert following != _link(first, OLDER)

    def test_a_batch_costs_one_bounded_query(
        self, next_client, demo_data, django_assert_num_queries
    ) -> None:
        href = _link(next_client.get(LOG_URL).content.decode(), OLDER)
        with django_assert_num_queries(1) as captured:
            _older(next_client, href)
        [query] = captured.captured_queries
        assert "LIMIT 21" in query["sql"]
        assert "OFFSET" not in query["sql"]
        assert "COUNT(" not in query["sql"]

    def test_following_every_token_reads_each_row_once(
        self, next_client, demo_data
    ) -> None:
        body = next_client.get(LOG_URL).content.decode()
        keys = _row_keys(body)
        more = _sentinel(body, "more")
        while "<a" in more:
            envelope = _older(next_client, _link(more, OLDER))
            keys += _row_keys(envelope.html_for_zone("audit-rows"))
            more = envelope.html_for_zone("audit-rows-more")

        assert keys == _log_order()
        assert len(keys) == demo_data
        assert "End of the log." in more
        assert 'data-next-key="audit-rows-more"' in more

    def test_a_deep_link_offers_the_rows_above_it(self, next_client, demo_data) -> None:
        href = _link(next_client.get(LOG_URL).content.decode(), OLDER)
        body = next_client.get(f"{LOG_URL}{href}").content.decode()

        assert _row_keys(body) == _log_order()[20:40]
        less = _sentinel(body, "less")
        assert " hidden" not in less
        assert 'data-next-target="audit-rows,audit-rows-less"' in less
        assert 'data-next-merge="prepend"' in less

    def test_the_previous_token_prepends_the_batch_above(
        self, next_client, demo_data
    ) -> None:
        href = _link(next_client.get(LOG_URL).content.decode(), OLDER)
        deep = next_client.get(f"{LOG_URL}{href}").content.decode()
        envelope = _newer(next_client, _link(_sentinel(deep, "less"), NEWER))

        assert envelope.op_verbs() == ["prepend", "prepend"]
        assert _row_keys(envelope.html_for_zone("audit-rows")) == _log_order()[:20]
        assert " hidden>" in envelope.html_for_zone("audit-rows-less")

    def test_a_restore_renders_the_batches_the_prune_keeps(
        self, next_client, demo_data
    ) -> None:
        more = _sentinel(next_client.get(LOG_URL).content.decode(), "more")
        hrefs = []
        for _batch in range(3):
            hrefs.append(_link(more, OLDER))
            more = _older(next_client, hrefs[-1]).html_for_zone("audit-rows-more")

        shallow = next_client.get(
            f"{LOG_URL}{hrefs[0]}&_restore=cursor"
        ).content.decode()
        assert _row_keys(shallow) == _log_order()[:40]
        assert " hidden>" in _sentinel(shallow, "less")

        deep = next_client.get(f"{LOG_URL}{hrefs[2]}&_restore=cursor").content.decode()
        assert _row_keys(deep) == _log_order()[20:80]
        assert " hidden" not in _sentinel(deep, "less")
        backs = re.search(r'data-next-backs="([^"]+)"', _sentinel(deep, "more"))
        assert backs is not None
        assert len(backs.group(1).split(" ")) == 2
        assert "restore" not in _link(_sentinel(deep, "more"), OLDER)

    @pytest.mark.parametrize(
        "cursor",
        ["garbage", "%21%21", "W10", "WzFd", "W2ZhbHNlLCJub3QgYSBkYXRlIiwxXQ"],
        ids=["no-json", "no-slug", "empty-list", "no-direction", "no-stamp"],
    )
    def test_a_malformed_cursor_falls_back_to_the_first_window(
        self, next_client, demo_data, cursor
    ) -> None:
        response = next_client.get(f"{LOG_URL}?cursor={cursor}")
        assert response.status_code == 200
        body = response.content.decode()
        assert _row_keys(body) == _log_order()[:20]
        assert " hidden>" in _sentinel(body, "less")

    def test_the_cursor_carries_the_kind_filter(self, next_client, demo_data) -> None:
        kind = AuditEntry.KIND_DISPATCHED
        body = next_client.get(f"{LOG_URL}?kind={kind}").content.decode()
        href = _link(_sentinel(body, "more"), OLDER)
        assert href.startswith(f"?kind={kind}&cursor=")

        envelope = _older(next_client, href)
        keys = _row_keys(body) + _row_keys(envelope.html_for_zone("audit-rows"))
        assert keys == _log_order(kind=kind)[:40]


class TestDemoData:
    """The demo log comes from a seed module rather than a migration."""

    def test_the_seed_command_fills_an_empty_log(self) -> None:
        out = StringIO()
        call_command("seed_demo", stdout=out)
        rows = AuditEntry.objects.count()
        assert f"Demo audit log seeded with {rows} rows." in out.getvalue()
        assert rows > REQUESTERS
        assert AccessRequest.objects.count() == REQUESTERS
        assert set(AuditEntry.objects.values_list("kind", flat=True)) == (
            AuditEntry.KINDS - {AuditEntry.KIND_RECEIPT_ISSUED}
        )

    def test_seeding_twice_keeps_one_copy(self, demo_data) -> None:
        out = StringIO()
        call_command("seed_demo", stdout=out)
        assert "seeded with 0 rows" in out.getvalue()
        assert AuditEntry.objects.count() == demo_data
        assert AccessRequest.objects.count() == REQUESTERS

    def test_the_rows_of_one_post_share_a_stamp(self, demo_data) -> None:
        stamps = AuditEntry.objects.values_list("created_at", flat=True)
        assert len(set(stamps)) < demo_data
        linked = AuditEntry.objects.filings()
        assert linked.count() == REQUESTERS


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
        assert "✓ saved" in body

    def test_long_saved_value_is_truncated_in_the_summary(self, next_client) -> None:
        long_name = "y" * 100
        _post_step(next_client, "identity", {**IDENTITY, "full_name": long_name})
        body = next_client.get("/request/scope/").content.decode()
        assert long_name not in body
        assert "y" * 59 + "\u2026" in body

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
            partial="access-wizard",
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
    """A blur probe morphs the wizard zone with the bound step and reports one field."""

    def test_bad_email_blur_morphs_the_wizard_zone_with_the_field_verdict(
        self, next_client
    ) -> None:
        before = AccessRequest.objects.count()
        response = _validate_field(
            next_client, "identity", "email", {**IDENTITY, "email": "not-an-email"}
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["access-wizard"]
        assert envelope.form_targets() == []
        meta = envelope.form_meta()
        assert meta is not None
        assert meta["valid"] is False
        assert meta["errors"] == {"email": ["Enter a valid email address."]}
        assert AccessRequest.objects.count() == before == 0

    def test_the_blur_zone_morph_carries_the_bound_step(self, next_client) -> None:
        response = _validate_field(
            next_client, "identity", "email", {**IDENTITY, "email": "not-an-email"}
        )
        html = envelope_of(response).html_for_zone("access-wizard")
        assert 'data-next-target="access-wizard"' in html
        assert 'data-step-section="identity" data-state="errors"' in html
        assert "Ada Lovelace" in html
        assert "Enter a valid email address." in html

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
    """The acknowledgement is a step field, so a bound re-render shows what was sent."""

    def test_the_first_render_ticks_the_acknowledgement(self, next_client) -> None:
        body = next_client.get("/request/identity/").content.decode()
        assert "checked" in _policy_input(body)

    def test_a_blur_zone_morph_returns_an_unticked_box_unticked(
        self, next_client
    ) -> None:
        response = _validate_field(next_client, "identity", "email", IDENTITY)
        html = envelope_of(response).html_for_zone("access-wizard")
        assert "checked" not in _policy_input(html)

    def test_a_blur_zone_morph_returns_a_ticked_box_ticked(self, next_client) -> None:
        response = _validate_field(
            next_client, "identity", "email", {**IDENTITY, "policy_acknowledged": "on"}
        )
        html = envelope_of(response).html_for_zone("access-wizard")
        assert "checked" in _policy_input(html)

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


class TestPageMetadata:
    """The landing keeps the site default, every page holding request data is `noindex`."""

    @pytest.mark.parametrize(
        ("path", "title", "noindex"),
        [
            ("/", "next.dj — Audit-trail forms", False),
            ("/request/identity/", "Request access · next.dj audit", True),
            ("/request/{pk}/audit/", "Request #{pk} · next.dj audit", True),
            ("/admin/audit/", "Audit log · next.dj audit", True),
            ("/request/{pk}/receipt/", "Receipt · Request #{pk} · next.dj audit", True),
        ],
        ids=["landing", "wizard", "request-audit", "audit-log", "receipt"],
    )
    def test_each_page_carries_its_title_and_robots(
        self,
        next_client,
        submitted_request: AccessRequest,
        path: str,
        title: str,
        *,
        noindex: bool,
    ) -> None:
        pk = submitted_request.pk
        assert_metadata(
            next_client.get(path.format(pk=pk)),
            title=title.format(pk=pk),
            robots="noindex" if noindex else None,
        )


OPEN_ACTIONS = frozenset({"access_request_wizard"})


class TestOpenActions:
    """Nobody signs in here, so every page with an action declares itself public."""

    def test_the_permission_table_lists_the_actions_as_public(self) -> None:
        out = StringIO()
        call_command("permissions", stdout=out)
        _header, *lines = out.getvalue().splitlines()
        rows = (re.split(r"\s{2,}", line.strip()) for line in lines)
        access = {
            name.partition(" ")[0]: rule
            for kind, name, rule, _sources in rows
            if kind == "action"
        }
        assert {name: access[name] for name in OPEN_ACTIONS} == dict.fromkeys(
            OPEN_ACTIONS, "public"
        )

    def test_the_declared_access_leaves_no_action_unguarded(self) -> None:
        assert "next.W129" not in {message.id for message in run_checks()}


def _unfiled_request() -> AccessRequest:
    return AccessRequest.objects.create(
        full_name="Grace Hopper",
        email="grace@example.com",
        team="Compilers",
        project_slug="compilers",
        reason="docs",
        expires_in_days=3,
    )


def _meta_ops(response) -> list[dict]:
    return [op for op in envelope_of(response).ops if op["op"] == "meta"]


class TestAuditPeek:
    """`(.)request/[int:access_request]/audit/` serves the `audit-peek` zone."""

    def test_landing_rows_open_the_peek_over_the_honest_url(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        anchor = find_anchor(
            next_client.get("/").content.decode(),
            href=f"/request/{submitted_request.pk}/audit/",
        )
        assert 'data-next-layer="audit-peek"' in anchor

    def test_a_plain_get_renders_the_real_page(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        body = next_client.get(f"/request/{submitted_request.pk}/audit/").content
        assert b"data-audit-table" in body
        assert b"data-audit-peek" not in body

    def test_the_zone_get_serves_the_intercept_with_its_title(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        response = next_client.get_zones(f"/request/{pk}/audit/", "audit-peek")

        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]
        assert "X-Next-Zone" in response["Vary"]
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph", "meta"]
        html = envelope.html_for_zone("audit-peek")
        assert "data-audit-peek" in html
        assert "Ada Lovelace" in html
        assert 'data-next-poll="5000"' in html
        assert "data-audit-table" not in html
        assert [op["title"] for op in _meta_ops(response)] == [
            f"Audit peek · Request #{pk} · next.dj audit"
        ]
        assert _meta_ops(response)[0]["robots"] == "noindex"

    def test_the_peek_links_to_the_full_page(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        response = next_client.get_zones(f"/request/{pk}/audit/", "audit-peek")
        anchor = find_anchor(
            envelope_of(response).html_for_zone("audit-peek"), text="Open full page"
        )
        assert f'href="/request/{pk}/audit/"' in anchor
        assert "data-next-layer" not in anchor

    def test_a_poll_of_the_nested_zone_keeps_serving_the_intercept(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        url = f"/request/{pk}/audit/"
        before = next_client.get_zones(url, "audit-peek-entries")
        AuditEntry.objects.create(
            action_name="reviewer_note",
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
            request=submitted_request,
        )
        after = next_client.get_zones(url, "audit-peek-entries")

        assert after.status_code == 200
        envelope = envelope_of(after)
        assert envelope.zone_targets() == ["audit-peek-entries"]
        html = envelope.html_for_zone("audit-peek-entries")
        assert "reviewer_note" in html
        assert "reviewer_note" not in envelope_of(before).html_for_zone(
            "audit-peek-entries"
        )
        assert (
            html.count("data-audit-peek-row") == submitted_request.audit_entries.count()
        )
        assert [op["title"] for op in _meta_ops(after)] == [
            f"Audit peek · Request #{pk} · next.dj audit"
        ]

    def test_an_empty_trail_says_so(self, next_client) -> None:
        unfiled = _unfiled_request()
        response = next_client.get_zones(
            f"/request/{unfiled.pk}/audit/", "audit-peek-entries"
        )
        html = envelope_of(response).html_for_zone("audit-peek-entries")
        assert "No rows linked yet" in html

    def test_an_unknown_zone_is_a_400(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        response = next_client.get_zones(
            f"/request/{submitted_request.pk}/audit/", "no-such-zone"
        )
        assert response.status_code == 400
        assert response.content == b"unknown zone"

    def test_a_row_hidden_by_visible_to_is_a_404(
        self, next_client, submitted_request: AccessRequest, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            AccessRequestQuerySet, "visible_to", lambda rows, _user: rows.none()
        )
        response = next_client.get_zones(
            f"/request/{submitted_request.pk}/audit/", "audit-peek"
        )
        assert response.status_code == 404

    def test_an_unknown_request_is_a_404(self, next_client) -> None:
        response = next_client.get_zones("/request/9999/audit/", "audit-peek")
        assert response.status_code == 404


class TestAuditLogPeek:
    """`admin/audit/(...)request/[int:access_request]/audit/` peeks from the log."""

    def test_linked_log_rows_open_the_peek(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        body = next_client.get(LOG_URL).content.decode()
        anchor = find_anchor(body, href=f"/request/{submitted_request.pk}/audit/")
        assert 'data-next-layer="audit-log-peek"' in anchor

    def test_the_trail_page_links_its_rows_without_a_peek(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        body = next_client.get(f"/request/{pk}/audit/").content.decode()
        anchor = find_anchor(body, text=f"request #{pk}")
        assert "data-next-layer" not in anchor

    def test_the_zone_get_serves_the_request_summary(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        response = next_client.get_zones(f"/request/{pk}/audit/", "audit-log-peek")

        assert response.status_code == 200
        html = envelope_of(response).html_for_zone("audit-log-peek")
        assert "data-audit-log-peek" in html
        assert "Need read access for analysis." in html
        assert "1 linked row" in html
        assert [op["title"] for op in _meta_ops(response)] == [
            f"Request #{pk} from the log · next.dj audit"
        ]


class TestReceipt:
    """`request/[int:access_request]/receipt/` has a `render()` body and a peek."""

    def test_the_trail_offers_the_receipt_peek_once_filed(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        body = next_client.get(f"/request/{pk}/audit/").content.decode()
        anchor = find_anchor(body, href=f"/request/{pk}/receipt/")
        assert 'data-next-layer="receipt-peek"' in anchor

    def test_an_unfiled_trail_offers_no_receipt(self, next_client) -> None:
        unfiled = _unfiled_request()
        body = next_client.get(f"/request/{unfiled.pk}/audit/").content.decode()
        assert f"/request/{unfiled.pk}/receipt/" not in body

    def test_a_plain_get_renders_the_render_body_in_the_layout(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        response = next_client.get(f"/request/{submitted_request.pk}/receipt/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "data-receipt" in body
        assert "data-receipt-peek" not in body
        assert f"AR-{submitted_request.pk:05d}" in body
        assert "<!DOCTYPE html>" in body

    def test_the_receipt_keeps_request_data_out_of_the_template_source(
        self, next_client
    ) -> None:
        unfiled = _unfiled_request()
        unfiled.full_name = "{{ 7|add:1 }}"
        unfiled.save()
        AuditEntry.objects.create(
            action_name=WIZARD_ACTION,
            kind=AuditEntry.KIND_DISPATCHED,
            source=AuditEntry.SOURCE_BACKEND,
            request=unfiled,
        )
        body = next_client.get(f"/request/{unfiled.pk}/receipt/").content.decode()
        assert "{{ 7|add:1 }} &lt;grace@example.com&gt;" in body

    def test_text_format_downloads_the_receipt(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        pk = submitted_request.pk
        response = next_client.get(f"/request/{pk}/receipt/?format=txt")
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response["Content-Disposition"] == (
            f'attachment; filename="access-request-{pk}.txt"'
        )
        assert response.content.decode().startswith("ACCESS REQUEST RECEIPT")

    def test_the_first_opening_writes_one_audit_row(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        issued = submitted_request.audit_entries.filter(
            kind=AuditEntry.KIND_RECEIPT_ISSUED
        )
        next_client.get(f"/request/{submitted_request.pk}/receipt/")
        next_client.get(f"/request/{submitted_request.pk}/receipt/?format=txt")
        assert issued.count() == 1

    def test_a_failing_download_takes_the_audit_row_back(
        self, next_client, submitted_request: AccessRequest, monkeypatch
    ) -> None:
        def broken(self: AccessRequest) -> str:
            raise OperationalError

        monkeypatch.setattr(AccessRequest, "receipt_text", broken)
        with pytest.raises(OperationalError):
            next_client.get(f"/request/{submitted_request.pk}/receipt/?format=txt")
        assert not AuditEntry.objects.filter(
            kind=AuditEntry.KIND_RECEIPT_ISSUED
        ).exists()

    def test_an_unfiled_request_has_no_receipt(self, next_client) -> None:
        unfiled = _unfiled_request()
        assert next_client.get(f"/request/{unfiled.pk}/receipt/").status_code == 404
        assert not AuditEntry.objects.filter(
            kind=AuditEntry.KIND_RECEIPT_ISSUED
        ).exists()

    def test_the_peek_zone_get_runs_render_once_then_serves_the_intercept(
        self, next_client, submitted_request: AccessRequest, monkeypatch
    ) -> None:
        calls: list[int] = []
        is_filed = AccessRequest.is_filed

        def counted(access_request: AccessRequest) -> bool:
            calls.append(access_request.pk)
            return is_filed(access_request)

        monkeypatch.setattr(AccessRequest, "is_filed", counted)
        pk = submitted_request.pk
        response = next_client.get_zones(f"/request/{pk}/receipt/", "receipt-peek")

        assert calls == [pk]
        assert response.status_code == 200
        html = envelope_of(response).html_for_zone("receipt-peek")
        assert "data-receipt-peek" in html
        assert f"AR-{pk:05d}" in html
        assert [op["title"] for op in _meta_ops(response)] == [
            f"Receipt preview · Request #{pk} · next.dj audit"
        ]
        assert _meta_ops(response)[0]["robots"] == "noindex"

    def test_a_render_denial_stops_the_peek(self, next_client) -> None:
        unfiled = _unfiled_request()
        response = next_client.get_zones(
            f"/request/{unfiled.pk}/receipt/", "receipt-peek"
        )
        assert response.status_code == 404

    def test_a_render_response_answers_the_zone_get_as_is(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        response = next_client.get_zones(
            f"/request/{submitted_request.pk}/receipt/?format=txt", "receipt-peek"
        )
        assert response["Content-Type"] == "text/plain; charset=utf-8"

    def test_a_zone_no_intercept_declares_is_a_dynamic_body_400(
        self, next_client, submitted_request: AccessRequest
    ) -> None:
        response = next_client.get_zones(
            f"/request/{submitted_request.pk}/receipt/", "no-such-zone"
        )
        assert response.status_code == 400
        assert response.content == b"zone in dynamic body"
