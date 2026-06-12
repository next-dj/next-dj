import pytest

from next.forms import FormActionNotFound
from next.forms.exceptions import _unknown_action_message


class TestFormActionNotFound:
    """FormActionNotFound carries the failing name and lookup context."""

    def test_is_a_lookup_error(self) -> None:
        """The exception subclasses LookupError, not KeyError."""
        exc = FormActionNotFound("Unknown form action 'x'.", name="x")
        assert isinstance(exc, LookupError)
        assert not isinstance(exc, KeyError)

    def test_message_is_unquoted(self) -> None:
        """str() returns the message verbatim."""
        exc = FormActionNotFound("Unknown form action 'x'.", name="x")
        assert str(exc) == "Unknown form action 'x'."

    def test_default_context_fields(self) -> None:
        """page_path defaults to None and suggestions to an empty tuple."""
        exc = FormActionNotFound("missing", name="missing_action")
        assert exc.name == "missing_action"
        assert exc.page_path is None
        assert exc.suggestions == ()

    def test_context_fields_are_stored(self) -> None:
        """name, page_path, and suggestions are exposed as attributes."""
        exc = FormActionNotFound(
            "missing",
            name="missing_action",
            page_path="/app/pages/page.py",
            suggestions=["missing_actions"],
        )
        assert exc.name == "missing_action"
        assert exc.page_path == "/app/pages/page.py"
        assert exc.suggestions == ("missing_actions",)

    def test_catchable_by_type(self) -> None:
        """The exception can be caught by its own type."""
        msg = "missing"
        with pytest.raises(FormActionNotFound):
            raise FormActionNotFound(msg, name="missing_action")


class TestUnknownActionMessage:
    """_unknown_action_message renders the lookup context and hints."""

    def test_without_suggestions(self) -> None:
        """No suggestions means no closest-matches sentence."""
        message = _unknown_action_message("vote", "/app/page.py")
        assert message == (
            "Unknown form action 'vote'. Searched page scope for "
            "/app/page.py and the shared registry."
        )

    def test_without_page_path(self) -> None:
        """A missing page path renders as 'no page'."""
        message = _unknown_action_message("vote", None)
        assert "Searched page scope for no page" in message

    def test_with_suggestions(self) -> None:
        """Suggestions append a closest-matches sentence."""
        message = _unknown_action_message("vot", None, ("vote", "veto"))
        assert message.endswith("Closest matches: 'vote', 'veto'.")
