"""The public exceptions of the boundaries area."""

from pathlib import Path
from typing import Final


OFF_MESSAGE: Final = "Boundaries are off, because grimp is not installed."
INSTALL_HINT: Final = "Install the next.dj[boundaries] extra to turn them on."


class BoundariesUnavailableError(RuntimeError):
    """The import graph needs grimp, which the environment does not install."""

    def __init__(self) -> None:
        """Say how to turn the boundaries on."""
        super().__init__(f"{OFF_MESSAGE} {INSTALL_HINT}")


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
    """A label names no application of the project."""

    def __init__(self, label: str, labels: tuple[str, ...]) -> None:
        """Name the label and the applications there are."""
        known = ", ".join(labels) or "none"
        super().__init__(
            f"{label!r} is no application of the project. Its applications: {known}."
        )
        self.label = label


__all__ = [
    "INSTALL_HINT",
    "OFF_MESSAGE",
    "BaselineError",
    "BoundariesUnavailableError",
    "ImportScanError",
    "UnknownAppError",
]
