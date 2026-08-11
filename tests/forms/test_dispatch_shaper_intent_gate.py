import pytest

from next.forms.manager import form_action_manager
from next.ports import partial_shaper_slot
from tests.support import IntentOnlyShaper


@pytest.fixture()
def intent_only_shaper():
    """Bind a shaper that refuses to shape, restore the real one after."""
    bound = partial_shaper_slot.get()
    shaper = IntentOnlyShaper()
    partial_shaper_slot.set(shaper)
    try:
        yield shaper
    finally:
        partial_shaper_slot.set(bound)


def _post(client, **extra):
    url = form_action_manager.get_action_url("simple_form")
    data = {"name": "Ada", "email": "", "_next_form_origin": "/"}
    return client.post(url, data=data, follow=False, **extra)


@pytest.mark.django_db()
class TestPlainSubmission:
    """A submission naming no validate field reads the intent and never shapes."""

    def test_submission_renders_in_full(
        self, client_no_csrf, intent_only_shaper
    ) -> None:
        response = _post(client_no_csrf)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert response.content == b"<p>test page</p>\n"

    def test_shaper_is_consulted_for_intent(
        self, client_no_csrf, intent_only_shaper
    ) -> None:
        _post(client_no_csrf)
        assert intent_only_shaper.calls["intent"] > 0
