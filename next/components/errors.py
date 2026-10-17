"""Exceptions the components area raises for a `component.py` it cannot load."""

from pathlib import Path


class ComponentModuleImportError(Exception):
    """A `component.py` body raised while importing.

    The original exception is the `__cause__`, and `file_path` names the failing file.
    """

    def __init__(self, file_path: Path) -> None:
        """Compose the message from the failing path."""
        super().__init__(f"{file_path} failed to import")
        self.file_path = file_path


__all__ = ["ComponentModuleImportError"]
