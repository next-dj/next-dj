"""Exception types raised by the form-action lookup machinery."""

from collections.abc import Iterable


def _unknown_action_message(
    name: str,
    page_path: str | None,
    suggestions: tuple[str, ...] = (),
) -> str:
    """Compose the lookup-failure message with optional close-match hints."""
    message = (
        f"Unknown form action {name!r}. Searched page scope for "
        f"{page_path or 'no page'} and the shared registry."
    )
    if not suggestions:
        return message
    rendered = ", ".join(repr(suggestion) for suggestion in suggestions)
    return f"{message} Closest matches: {rendered}."


class FormActionNotFound(LookupError):  # noqa: N818
    """No registered form action matches the requested name."""

    def __init__(
        self,
        message: str,
        *,
        name: str,
        page_path: str | None = None,
        suggestions: Iterable[str] = (),
    ) -> None:
        """Store the failing name and lookup context alongside the message."""
        super().__init__(message)
        self.name = name
        self.page_path = page_path
        self.suggestions = tuple(suggestions)


__all__ = ["FormActionNotFound"]
