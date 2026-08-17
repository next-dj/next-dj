import pytest

from next.forms.manager import form_action_manager


def _post(client, **extra):
    url = form_action_manager.get_action_url("simple_form")
    data = {"name": "", "_next_form_origin": "/"}
    return client.post(url, data=data, follow=False, **extra)


@pytest.mark.django_db()
class TestPlainSubmission:
    """A non-partial submission reads the intent and never shapes."""

    def test_invalid_submission_renders_the_origin_page(
        self, client_no_csrf, intent_only_shaper
    ) -> None:
        response = _post(client_no_csrf)
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    def test_shaper_is_consulted_for_intent(
        self, client_no_csrf, intent_only_shaper
    ) -> None:
        _post(client_no_csrf)
        assert intent_only_shaper.calls["intent"] > 0
