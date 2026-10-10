"""The public exceptions of the scripts area."""

from pathlib import Path


class ScriptsSourceImportError(Exception):
    """A `scripts.py` failed to import, so its tree renders none of its scripts."""

    def __init__(self, path: Path) -> None:
        """Name the source that failed."""
        super().__init__(f"{path} failed to import, so its page tree runs no scripts")
        self.path = path


__all__ = ["ScriptsSourceImportError"]
