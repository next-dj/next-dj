import pytest

from next.testing import NextClient
from tests.forms import actions


# `actions` registers the baseline form actions on import. Bind it so the
# module survives unused-import cleanup whatever the collection order is.
_BASELINE_ACTIONS = actions


class TestNextClient:
    """NextClient extends django.test.Client with form-action shortcuts."""

    def test_get_action_url_returns_known_url(self) -> None:
        client = NextClient()
        url = client.get_action_url("simple_form")
        assert "_next/form/" in url

    def test_post_action_dispatches_form(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form_redirect",
            {"name": "Carol"},
        )
        assert response.status_code in (200, 302)

    @pytest.mark.django_db()
    def test_post_action_origin_fills_hidden_field(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""}, origin="/")
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    def test_post_action_without_origin_keeps_protocol_raw(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""})
        assert response.status_code == 400

    @pytest.mark.django_db()
    def test_post_action_data_origin_wins_over_keyword(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form",
            {"name": "", "_next_form_origin": "/"},
            origin="/no/such/route/",
        )
        assert response.status_code == 200
