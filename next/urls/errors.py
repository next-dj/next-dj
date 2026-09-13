"""Exceptions the URL area raises for a route or a router it cannot build."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, override

from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    from pathlib import Path


class URLParameterError(ValueError):
    """Raised when the parser refuses to turn a bracket segment into a route.

    The parser knows the route but not the file it came from, so one base
    carries the page file and every refusal reports it the same way.
    """

    def __init__(
        self, param_name: str, url_path: str, file_path: Path | None = None
    ) -> None:
        """Build the message from the refused name, its route, and the page file."""
        self.param_name = param_name
        self.url_path = url_path
        self.file_path = file_path
        message = self._reason()
        if file_path is not None:
            message = f"{message} Page file: {file_path}."
        super().__init__(message)

    def with_file(self, file_path: Path) -> Self:
        """Return the same refusal with `file_path` named in its message."""
        return type(self)(self.param_name, self.url_path, file_path=file_path)

    def _reason(self) -> str:
        """Return the sentence explaining why the route cannot be built."""
        return (
            f"URL parameter '{self.param_name}' in URL pattern "
            f"'{self.url_path}' cannot be turned into a Django route."
        )


class DuplicateURLParameterError(URLParameterError):
    """Raised when bracket segments in one route conflict after normalisation.

    Covers a repeated normalised parameter name (`-` maps to `_`) and a
    second `[[wildcard]]` segment, both of which Django would otherwise
    reject only at resolve time or resolve ambiguously.
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

    Django compiles a route the moment the pattern is built, so a name that is
    no Python identifier would otherwise surface as an `ImproperlyConfigured`
    traceback far from the directory that named it.
    """

    @override
    def _reason(self) -> str:
        """Name the refused parameter and the rule Django applies to it."""
        return (
            f"URL parameter '{self.param_name}' in URL pattern "
            f"'{self.url_path}' is no valid Python identifier once '-' is read "
            "as '_'. Django refuses such a name when it compiles the route."
        )


class RouterConstructionError(ImproperlyConfigured):
    """Raised when a router class refuses the arguments the factory passes."""

    def __init__(self, backend_name: str, exc: TypeError) -> None:
        """Store the router that refused and the signature mismatch it reported."""
        self.backend_name = backend_name
        super().__init__(
            f"{backend_name} does not take the arguments RouterFactory "
            f"builds a router with: {exc}"
        )


__all__ = [
    "DuplicateURLParameterError",
    "InvalidURLParameterError",
    "RouterConstructionError",
    "URLParameterError",
]
