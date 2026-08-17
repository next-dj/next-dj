from django.test import Client


class TestPlainPageRequest:
    """A request naming no zone reads the intent and never shapes."""

    def test_page_renders_in_full(self, intent_only_shaper) -> None:
        response = Client().get("/zoned/")
        assert response.status_code == 200
        assert b"<h1>zoned page</h1>" in response.content

    def test_shaper_is_consulted_for_intent(self, intent_only_shaper) -> None:
        Client().get("/zoned/")
        assert intent_only_shaper.calls["intent"] > 0
