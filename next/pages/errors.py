"""Exceptions the pages area raises for a page module it cannot load."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


class PageModuleImportError(Exception):
    """A `page.py` body raised while importing.

    The original exception travels as `__cause__` and the offending path as `file_path`.
    """

    def __init__(self, file_path: Path) -> None:
        """Compose the message from the failing path."""
        super().__init__(f"{file_path} failed to import")
        self.file_path = file_path


__all__ = ["PageModuleImportError"]
