"""Exceptions the URL area raises for a route it cannot build."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, override


if TYPE_CHECKING:
    from pathlib import Path


class URLParameterError(ValueError):
    """Raised when the parser refuses to turn a bracket segment into a route.

    The parser knows the route but not the file, so one base carries the page file.
    """

    def __init__(
        self, param_name: str, url_path: str, file_path: Path | None = None
    ) -> None:
        """Store the refused name, its route, and the page file when one is known."""
        self.param_name = param_name
        self.url_path = url_path
        self.file_path = file_path
        super().__init__(self._reason())

    def with_file(self, file_path: Path) -> Self:
        """Return the same refusal with `file_path` named in its message.

        Cloned past the constructor, so a subclass may take a shape of its own.
        """
        named = type(self).__new__(type(self))
        named.args = self.args
        named.__dict__.update(self.__dict__)
        named.file_path = file_path
        return named

    @override
    def __str__(self) -> str:
        """Render the reason, naming the page file once one is known."""
        reason = super().__str__()
        if self.file_path is None:
            return reason
        return f"{reason} Page file: {self.file_path}."

    def _reason(self) -> str:
        """Return the sentence explaining why the route cannot be built."""
        return (
            f"URL parameter '{self.param_name}' in URL pattern "
            f"'{self.url_path}' cannot be turned into a Django route."
        )


class DuplicateURLParameterError(URLParameterError):
    """Raised when bracket segments in one route conflict after normalisation.

    A repeated normalised name and a second `[[wildcard]]` both resolve ambiguously.
    """

    @override
    def _reason(self) -> str:
        """Name the conflicting parameter and the rule it breaks."""
        return (
            f"Duplicate URL parameter '{self.param_name}' in URL pattern "
            f"'{self.url_path}'. Parameter names must be unique after '-' to "
            "'_' normalisation and a route can hold at most one [[wildcard]] "
            "segment."
        )


class InvalidURLParameterError(URLParameterError):
    """Raised when a bracket segment names something Django refuses as a route.

    Django compiles a route as the pattern is built, so a bad name would otherwise
    surface as an `ImproperlyConfigured` far from the directory that named it.
    """

    @override
    def _reason(self) -> str:
        """Name the refused parameter and the rule Django applies to it."""
        return (
            f"URL parameter '{self.param_name}' in URL pattern "
            f"'{self.url_path}' is no valid Python identifier once '-' is read "
            "as '_'. Django refuses such a name when it compiles the route."
        )


__all__ = [
    "DuplicateURLParameterError",
    "InvalidURLParameterError",
    "URLParameterError",
]
