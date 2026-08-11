import pytest
from django.test import Client

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


class TestPlainPageRequest:
    """A request naming no zone reads the intent and never shapes."""

    def test_page_renders_in_full(self, intent_only_shaper) -> None:
        response = Client().get("/zoned/")
        assert response.status_code == 200
        assert b"<h1>zoned page</h1>" in response.content

    def test_shaper_is_consulted_for_intent(self, intent_only_shaper) -> None:
        Client().get("/zoned/")
        assert intent_only_shaper.calls["intent"] > 0
