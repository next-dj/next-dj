import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from access.backends import _safe_form_payload, _step_from_origin
from access.models import AccessRequest, AuditEntry
from access.receivers import _on_form_access_denied
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpRequest, QueryDict


pytestmark = pytest.mark.django_db


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
    VIEWS_ROOT / "_blocks" / "audit_row" / "component.py", "audit_audit_row"
)
_step_section = _load(
    VIEWS_ROOT / "request" / "[step]" / "_blocks" / "step_section" / "component.py",
    "audit_step_section",
)
_admin_audit = _load(VIEWS_ROOT / "admin" / "audit" / "page.py", "audit_admin_audit")


def _audit_request(*, zone: str | None, kind: str = "") -> HttpRequest:
    request = HttpRequest()
    request.method = "GET"
    request.GET = QueryDict(f"kind={kind}" if kind else "")
    if zone is not None:
        request.META["HTTP_X_NEXT_REQUEST"] = "1"
        request.META["HTTP_X_NEXT_ZONE"] = zone
    return request


def _wizard(step: str, stored: dict[str, dict[str, object]] | None = None):
    request = HttpRequest()
    request.session = SessionStore()
    wizard = _step_page.AccessRequestWizard(
        request=request, url_kwargs={"step": step}, base_path=f"/request/{step}/"
    )
    for name, data in (stored or {}).items():
        wizard.save_step(name, data)
    return wizard


@pytest.fixture()
def make_audit_entry():
    def _make(**fields) -> AuditEntry:
        return AuditEntry(
            **{
                "action_name": "x",
                "kind": AuditEntry.KIND_DISPATCHED,
                "source": AuditEntry.SOURCE_BACKEND,
                **fields,
            }
        )

    return _make


@pytest.fixture()
def create_audit_entry(make_audit_entry):
    def _create(**fields) -> AuditEntry:
        entry = make_audit_entry(**fields)
        entry.save()
        return entry

    return _create


@pytest.fixture()
def create_access_request():
    def _create(**fields) -> AccessRequest:
        return AccessRequest.objects.create(
            **{
                "full_name": "Grace Hopper",
                "email": "grace@example.com",
                "team": "Compilers",
                "project_slug": "compilers",
                "reason": "docs",
                "expires_in_days": 3,
                **fields,
            }
        )

    return _create


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

    @pytest.mark.parametrize(
        ("origin", "expected"),
        [
            ("/request/scope/", "scope"),
            ("/request/identity/?just=1", "identity"),
            (None, ""),
            ("request/identity/", ""),
            ("/no/such/page/", ""),
            ("/", ""),
        ],
        ids=[
            "step_kwarg_from_path",
            "query_string_ignored",
            "missing_origin",
            "relative_origin",
            "unroutable_origin",
            "origin_without_step_kwarg",
        ],
    )
    def test_step_from_origin(self, origin, expected) -> None:
        assert _step_from_origin(self._request(origin)) == expected


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

    def test_audit_entry_str_carries_source_and_kind(self, make_audit_entry) -> None:
        entry = make_audit_entry(action_name="access_request_wizard")
        entry.created_at = datetime(2026, 4, 25, 12, 30, 0, tzinfo=UTC)
        rendered = str(entry)
        assert "backend/dispatched" in rendered
        assert "access_request_wizard" in rendered
        assert "2026-04-25 12:30:00" in rendered


class TestWizardSteps:
    """The three wizard steps map onto disjoint slices of the model fields."""

    @pytest.mark.parametrize(
        ("step", "expected_fields"),
        [
            (_step_page.IdentityStep, ["full_name", "email", "team"]),
            (_step_page.ScopeStep, ["project_slug", "reason", "expires_in_days"]),
            (_step_page.ApprovalStep, []),
        ],
        ids=["identity", "scope", "approval"],
    )
    def test_step_owns_its_own_fields(self, step, expected_fields) -> None:
        assert list(step.base_fields) == expected_fields

    def test_wizard_declares_three_ordered_steps(self) -> None:
        names = [name for name, _ in _step_page.AccessRequestWizard.Meta.steps]
        assert names == ["identity", "scope", "approval"]


class TestWizardPermissionHook:
    """`check_permissions` gates each step POST on the acknowledgement field."""

    @staticmethod
    def _request(*, acknowledged: bool, validation_probe: bool = False) -> HttpRequest:
        request = HttpRequest()
        request.method = "POST"
        query = "policy_acknowledged=on" if acknowledged else ""
        request.POST = QueryDict(query)
        if validation_probe:
            request.META["HTTP_X_NEXT_REQUEST"] = "1"
            request.META["HTTP_X_NEXT_VALIDATE"] = "name"
        return request

    @pytest.mark.parametrize(
        ("acknowledged", "validation_probe", "expected"),
        [(False, False, False), (True, False, True), (False, True, True)],
        ids=["unacknowledged", "acknowledged", "validation_probe"],
    )
    def test_check_permissions(self, acknowledged, validation_probe, expected) -> None:
        request = self._request(
            acknowledged=acknowledged, validation_probe=validation_probe
        )
        assert _step_page.AccessRequestWizard.check_permissions(request) is expected


@pytest.mark.django_db()
class TestAccessDeniedReceiver:
    """`_on_form_access_denied` records the denial layer and reason."""

    def test_receiver_stores_layer_and_reason(self) -> None:
        _on_form_access_denied(
            action_name="access_request_wizard", layer="view", reason="denied"
        )
        row = AuditEntry.objects.get(kind=AuditEntry.KIND_ACCESS_DENIED)
        assert row.source == AuditEntry.SOURCE_SIGNAL
        assert row.access_layer == "view"
        assert row.access_reason == "denied"
        assert row.action_name == "access_request_wizard"

    def test_access_denied_str_carries_source_and_kind(self, make_audit_entry) -> None:
        entry = make_audit_entry(
            action_name="access_request_wizard",
            kind=AuditEntry.KIND_ACCESS_DENIED,
            source=AuditEntry.SOURCE_SIGNAL,
        )
        entry.created_at = datetime(2026, 4, 25, 12, 30, 0, tzinfo=UTC)
        assert "signal/access_denied" in str(entry)


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

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"redirect": "/", "team": "Computing", "email": "a@b"}, ["email", "team"]),
            (None, []),
        ],
        ids=["dict_payload_drops_redirect", "non_dict_payload"],
    )
    def test_payload_keys(self, make_audit_entry, payload, expected) -> None:
        entry = make_audit_entry(kind=AuditEntry.KIND_REQUEST_STARTED, payload=payload)
        assert sorted(_audit_row.payload_keys(entry)) == expected

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"payload": {}}, "—"),
            (
                {
                    "kind": AuditEntry.KIND_ACCESS_DENIED,
                    "source": AuditEntry.SOURCE_SIGNAL,
                    "access_layer": "view",
                    "access_reason": "denied",
                },
                "view/denied",
            ),
        ],
        ids=["no_metrics", "access_denial_layer_and_reason"],
    )
    def test_summary(self, make_audit_entry, fields, expected) -> None:
        assert _audit_row.summary(make_audit_entry(**fields)) == expected

    def test_summary_pluralisation_matches_error_count(self, make_audit_entry) -> None:
        entry = make_audit_entry(
            kind=AuditEntry.KIND_VALIDATION_FAILED,
            source=AuditEntry.SOURCE_SIGNAL,
            error_count=1,
            field_names=["email"],
        )
        rendered = _audit_row.summary(entry)
        assert "1 error" in rendered
        assert "errors" not in rendered
        assert "email" in rendered

    def test_kind_class_marks_access_denied_as_rose(self, make_audit_entry) -> None:
        entry = make_audit_entry(
            kind=AuditEntry.KIND_ACCESS_DENIED, source=AuditEntry.SOURCE_SIGNAL
        )
        assert _audit_row.kind_class(entry) == "bg-rose-100 text-rose-800"


class TestStepFormValidation:
    """The model-backed steps validate only their own fields."""

    def test_identity_step_requires_email(self) -> None:
        form = _step_page.IdentityStep(
            data={"full_name": "Ada", "email": "", "team": "Computing"}
        )
        assert not form.is_valid()
        assert "email" in form.errors

    def test_scope_step_accepts_valid_payload(self) -> None:
        form = _step_page.ScopeStep(
            data={"project_slug": "engine", "reason": "reads", "expires_in_days": "7"}
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
        form = _step_page.IdentityStep(data={"full_name": "", "email": "", "team": ""})
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
                }
            },
        )
        rendered = _step_section.render(wizard.current_form(), wizard)
        assert 'data-step-section="scope" data-state="saved"' in rendered
        assert "..." in rendered
        assert long_reason not in rendered


class TestLandingPage:
    """The landing page exposes the most recent requests and audit rows."""

    def test_landing_lists_recent_request_and_audit_summaries(
        self, client, create_access_request, create_audit_entry
    ) -> None:
        create_access_request()
        create_audit_entry(action_name="access_request_wizard")
        response = client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "Grace Hopper" in body
        assert "compilers" in body
        assert "access_request_wizard" in body

    def test_landing_lists_newest_requests_first(
        self, client, create_access_request
    ) -> None:
        for index in range(6):
            create_access_request(
                full_name=f"Requester {index}",
                email=f"person{index}@example.com",
                project_slug=f"proj-{index}",
            )
        body = client.get("/").content.decode()
        assert "Requester 5" in body
        assert "Requester 0" not in body


@pytest.mark.django_db()
class TestLazyAuditEntries:
    """The `entries` provider runs its query only for the lazy zone request."""

    def test_full_render_request_returns_none_without_querying(
        self, create_audit_entry
    ) -> None:
        create_audit_entry()
        assert _admin_audit.entries(_audit_request(zone=None)) is None

    def test_zone_request_loads_the_rows(self, create_audit_entry) -> None:
        create_audit_entry()
        rows = _admin_audit.entries(_audit_request(zone="audit-table"))
        assert rows is not None
        assert len(rows) == 1

    def test_zone_request_applies_the_kind_filter(self, create_audit_entry) -> None:
        create_audit_entry()
        create_audit_entry(
            action_name="y",
            kind=AuditEntry.KIND_VALIDATION_FAILED,
            source=AuditEntry.SOURCE_SIGNAL,
        )
        rows = _admin_audit.entries(
            _audit_request(zone="audit-table", kind=AuditEntry.KIND_VALIDATION_FAILED)
        )
        assert rows is not None
        assert [r.kind for r in rows] == [AuditEntry.KIND_VALIDATION_FAILED]
