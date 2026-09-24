"""Exceptions the pages area raises for a page module it cannot load or merge."""

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


class PageContextShapeError(TypeError):
    """A keyless `@context` answered something other than a mapping.

    The merge would otherwise fail inside `dict.update` without naming the callable.
    """

    def __init__(self, context_name: str, file_path: Path) -> None:
        """Compose the message from the callable and the page it was building."""
        super().__init__(
            f"The keyless `@context` {context_name} returned a non-mapping "
            f"while building the context of {file_path}."
        )
        self.context_name = context_name
        self.file_path = file_path


__all__ = ["PageContextShapeError", "PageModuleImportError"]
