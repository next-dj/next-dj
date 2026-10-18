"""The public exceptions of the boundaries area."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


class ImportGraphError(ImproperlyConfigured):
    """`BOUNDARIES_IMPORT_GRAPH` names a backend that cannot read the import graph."""

    def __init__(self, reason: str) -> None:
        """Keep the reason the backend is refused."""
        super().__init__(f"NEXT_FRAMEWORK['BOUNDARIES_IMPORT_GRAPH'] {reason}")
        self.reason = reason


class ImportScanError(RuntimeError):
    """The import graph of the project packages cannot be built from their sources."""

    def __init__(self, reason: str, file: str = "", line: int = 0) -> None:
        """Keep the reason the scan stopped and the file and line it names."""
        where = f"{file}, line {line}: " if file else ""
        super().__init__(
            f"The import graph of the project cannot be built. {where}{reason}"
        )
        self.reason = reason
        self.file = file
        self.line = line


class BaselineError(ValueError):
    """The `boundaries.toml` ratchet cannot be read or written."""

    def __init__(self, path: Path | None, reason: str) -> None:
        """Name the file and the reason it is refused."""
        where = "boundaries.toml" if path is None else str(path)
        super().__init__(f"{where} cannot be used. {reason}")
        self.path = path
        self.reason = reason


class UnknownAppError(LookupError):
    """A label or a name that names no application of the project."""

    def __init__(self, key: str, labels: tuple[str, ...]) -> None:
        """Name the key and the applications there are."""
        known = ", ".join(labels) or "none"
        super().__init__(
            f"{key!r} is no application of the project. Its applications: {known}."
        )
        self.key = key


class SyncError(ValueError):
    """A `depends_on` that `next boundaries sync` cannot rewrite by itself."""

    def __init__(
        self,
        file: str,
        line: int,
        reason: str,
        remedy: str = "Edit depends_on there by hand.",
    ) -> None:
        """Name the file and line, the reason sync leaves it and what to do instead."""
        where = f"{file}, line {line}: " if line else f"{file}: "
        super().__init__(f"{where}{reason} {remedy}")
        self.file = file
        self.line = line
        self.reason = reason


__all__ = [
    "BaselineError",
    "ImportGraphError",
    "ImportScanError",
    "SyncError",
    "UnknownAppError",
]
