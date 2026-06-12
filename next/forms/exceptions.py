"""Exception types raised by the form-action lookup machinery."""

from collections.abc import Iterable


class FormActionNotFound(LookupError): # noqa: N818
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
