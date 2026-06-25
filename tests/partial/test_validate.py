import pytest
from django.contrib.auth import get_user_model

from next.partial.headers import CONTENT_TYPE, VALIDATE
from next.partial.signals import field_validated
from next.testing import NextClient, envelope_of
from tests.support import CountingWizardBackend, action_uid


User = get_user_model()

_VALIDATE_HEADER = f"HTTP_{VALIDATE.upper().replace('-', '_')}"


@pytest.fixture()
def email_blur(next_client):
    """Blur-validate the email field of the sample form once for the class."""
    return next_client.post_action(
        "validate_form",
        {"email": "bad"},
        origin="/",
        partial=True,
        **{_VALIDATE_HEADER: "email"},
    )


class TestValidateOnlyEnvelope:
    """A validate request returns a 200 form morph without running the handler."""

    def test_status_and_content_type(self, email_blur) -> None:
        assert email_blur.status_code == 200
        assert email_blur["Content-Type"] == CONTENT_TYPE

    def test_envelope_morphs_the_form_by_uid(self, email_blur) -> None:
        envelope = envelope_of(email_blur)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.form_targets() == [action_uid("validate_form")]

    def test_no_invalid_form_header(self, email_blur) -> None:
        assert "X-Next-Form" not in email_blur


class TestValidateErrorFiltering:
    """The validate pass surfaces only the requested fields' errors."""

    def test_requested_field_keeps_its_error(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert "email" in meta["errors"]

    def test_unfilled_field_gets_no_required_error(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert "name" not in meta["errors"]

    def test_non_field_errors_are_always_dropped(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert "__all__" not in meta["errors"]

    def test_file_field_is_dropped_from_targets(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "validate_form",
            {"email": "ok@example.com"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email,avatar"},
        )
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert "avatar" not in meta["errors"]


class TestValidateBehindGuard:
    """A guarded validate request denies an anonymous caller, no envelope."""

    def test_anonymous_validate_is_denied_not_an_envelope(
        self, next_client: NextClient
    ) -> None:
        received: list[dict] = []

        def _record(**kwargs: object) -> None:
            received.append(kwargs)

        field_validated.connect(_record)
        try:
            response = next_client.post_action(
                "guarded_validate_form",
                {"email": "bad"},
                origin="/",
                partial=True,
                **{_VALIDATE_HEADER: "email"},
            )
        finally:
            field_validated.disconnect(_record)
        assert response["Content-Type"] != CONTENT_TYPE
        assert received == []

    @pytest.mark.django_db()
    def test_authenticated_validate_returns_an_envelope(
        self, next_client: NextClient
    ) -> None:
        user = User.objects.create_user(username="ada", password="secret")
        next_client.force_login(user)
        response = next_client.post_action(
            "guarded_validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE


class TestValidateBehindViewPermissions:
    """A validate request denied by the view-permission layer runs no validator.

    This form carries no action guard, only a `check_permissions` view
    hook, so the denial proves the second authorization layer stops an
    anonymous blur on its own, independent of the action guard. The hook
    runs before the form binds, so the unique-email validator is never an
    anonymous brute-force oracle.
    """

    def test_anonymous_validate_is_denied_not_an_envelope(
        self, next_client: NextClient
    ) -> None:
        received: list[dict] = []

        def _record(**kwargs: object) -> None:
            received.append(kwargs)

        field_validated.connect(_record)
        try:
            response = next_client.post_action(
                "view_guard_validate_form",
                {"email": "taken@example.com"},
                origin="/",
                partial=True,
                **{_VALIDATE_HEADER: "email"},
            )
        finally:
            field_validated.disconnect(_record)
        assert response["Content-Type"] != CONTENT_TYPE
        assert received == []

    @pytest.mark.django_db()
    def test_authenticated_validate_runs_the_validator_behind_the_hook(
        self, next_client: NextClient
    ) -> None:
        user = User.objects.create_user(username="ida", password="secret")
        next_client.force_login(user)
        response = next_client.post_action(
            "view_guard_validate_form",
            {"email": "taken@example.com"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert meta["errors"]["email"] == ["address already registered"]


@pytest.mark.django_db()
class TestValidateCsrfMeta:
    """The CSRF meta rides the validate envelope only on a token rotation."""

    def test_rotation_stamps_the_csrf_payload(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "rotating_validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        envelope = envelope_of(response).data
        assert "csrf" in envelope
        assert envelope["csrf"]["token"]

    def test_no_rotation_leaves_no_csrf_meta(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "validate_form",
            {"email": "bad"},
            origin="/",
            partial=True,
            **{_VALIDATE_HEADER: "email"},
        )
        assert "csrf" not in envelope_of(response).data


class TestValidateInsideAZone:
    """A validate request from a form inside a zone morphs that zone."""

    def test_zone_morph_replaces_extract(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "zoned_rename_form",
            {"title": ""},
            origin="/board_settings/",
            partial=True,
            zones="rename-board",
            **{_VALIDATE_HEADER: "title"},
        )
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.zone_targets() == ["rename-board"]


@pytest.mark.django_db()
class TestValidateOnAWizardStep:
    """A validate request on a wizard step shapes a morph without saving.

    The wizard storage stays empty because the handler and `save_step`
    never run on a blur, so a draft only lands on a real submit.
    """

    def test_wizard_validate_returns_an_envelope(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": ""},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
            **{_VALIDATE_HEADER: "name"},
        )
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        assert envelope_of(response).op_verbs() == ["morph"]

    def test_wizard_validate_does_not_advance(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
            **{_VALIDATE_HEADER: "name"},
        )
        html = envelope_of(response).html_for_zone("wizard-zone")
        assert 'name="name"' in html
        assert 'name="scope"' not in html

    def test_wizard_validate_writes_no_storage(
        self, next_client: NextClient, counting_wizard_backend: CountingWizardBackend
    ) -> None:
        next_client.post_action(
            "step_wizard",
            {"name": "Ada"},
            origin="/wizard/identity/",
            partial=True,
            zones="wizard-zone",
            **{_VALIDATE_HEADER: "name"},
        )
        assert counting_wizard_backend.saves == 0
        assert counting_wizard_backend.clears == 0


class TestValidateSignalAndFieldNames:
    """The validate signal fires behind the guard with the scrubbed fields."""

    def test_signal_reports_the_requested_field_names(
        self, next_client: NextClient
    ) -> None:
        received: list[dict] = []

        def _record(**kwargs: object) -> None:
            received.append(kwargs)

        field_validated.connect(_record)
        try:
            next_client.post_action(
                "validate_form",
                {"email": "bad"},
                origin="/",
                partial=True,
                **{_VALIDATE_HEADER: "email,avatar"},
            )
        finally:
            field_validated.disconnect(_record)
        assert len(received) == 1
        assert received[0]["field_names"] == ("email",)
        assert received[0]["error_count"] == 1
