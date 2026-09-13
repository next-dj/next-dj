import pytest

from next.partial.headers import VALIDATE
from next.testing import NextClient, envelope_of


_VALIDATE_HEADER = f"HTTP_{VALIDATE.upper().replace('-', '_')}"
_EMAIL_ERROR = "Enter a valid email address."


class TestPlainFormInsideATagRenderedZone:
    """A zone body rendering its form through `{% form %}` gets the bound form."""

    def test_validate_keeps_the_posted_values(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "tagged_profile_form",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagzone/",
            partial=True,
            zones="profile",
            **{_VALIDATE_HEADER: "email"},
        )
        html = envelope_of(response).html_for_zone("profile")
        assert 'value="Ada"' in html
        assert 'value="bad"' in html

    def test_validate_renders_the_requested_error(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "tagged_profile_form",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagzone/",
            partial=True,
            zones="profile",
            **{_VALIDATE_HEADER: "email"},
        )
        html = envelope_of(response).html_for_zone("profile")
        assert _EMAIL_ERROR in html

    def test_invalid_submit_keeps_the_posted_values(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "tagged_profile_form",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagzone/",
            partial=True,
            zones="profile",
        )
        html = envelope_of(response).html_for_zone("profile")
        assert 'value="Ada"' in html
        assert 'value="bad"' in html

    def test_invalid_submit_renders_the_error(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "tagged_profile_form",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagzone/",
            partial=True,
            zones="profile",
        )
        html = envelope_of(response).html_for_zone("profile")
        assert _EMAIL_ERROR in html


@pytest.mark.django_db()
class TestWizardStepInsideATagRenderedZone:
    """A wizard step re-rendered through `{% form %}` keeps its bound step form."""

    def test_validate_keeps_the_posted_values(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "tagged_wizard",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagwizard/identity/",
            partial=True,
            zones="signup",
            **{_VALIDATE_HEADER: "email"},
        )
        html = envelope_of(response).html_for_zone("signup")
        assert 'value="Ada"' in html
        assert 'value="bad"' in html

    def test_validate_renders_the_requested_error(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "tagged_wizard",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagwizard/identity/",
            partial=True,
            zones="signup",
            **{_VALIDATE_HEADER: "email"},
        )
        html = envelope_of(response).html_for_zone("signup")
        assert _EMAIL_ERROR in html

    def test_validate_keeps_the_wizard_reads_working(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "tagged_wizard",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagwizard/identity/",
            partial=True,
            zones="signup",
            **{_VALIDATE_HEADER: "email"},
        )
        html = envelope_of(response).html_for_zone("signup")
        assert 'data-step="identity"' in html

    def test_invalid_submit_keeps_the_posted_values(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "tagged_wizard",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagwizard/identity/",
            partial=True,
            zones="signup",
        )
        html = envelope_of(response).html_for_zone("signup")
        assert 'value="Ada"' in html
        assert 'value="bad"' in html

    def test_invalid_submit_renders_the_error(self, next_client: NextClient) -> None:
        response = next_client.post_action(
            "tagged_wizard",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagwizard/identity/",
            partial=True,
            zones="signup",
        )
        html = envelope_of(response).html_for_zone("signup")
        assert _EMAIL_ERROR in html

    def test_invalid_submit_keeps_the_wizard_reads_working(
        self, next_client: NextClient
    ) -> None:
        response = next_client.post_action(
            "tagged_wizard",
            {"full_name": "Ada", "email": "bad"},
            origin="/tagwizard/identity/",
            partial=True,
            zones="signup",
        )
        html = envelope_of(response).html_for_zone("signup")
        assert 'data-step="identity"' in html
