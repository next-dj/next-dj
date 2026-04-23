from next.testing import NextClient


class TestNextClient:
    """NextClient extends django.test.Client with form-action shortcuts."""

    def test_get_action_url_returns_known_url(self) -> None:
        client = NextClient()
        url = client.get_action_url("test_submit")
        assert "_next/form/" in url

    def test_post_action_dispatches_form(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "test_redirect",
            {"name": "Carol", "email": "carol@example.com"},
        )
        assert response.status_code in (200, 302)
