"""Exceptions the pages area raises for a page module it cannot load or merge."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


class PageModuleImportError(Exception):
    """A `page.py` body raised while importing.

    The original exception is the `__cause__`, and `file_path` names the failing file.
    """

    def __init__(self, file_path: Path) -> None:
        """Compose the message from the failing path."""
        super().__init__(f"{file_path} failed to import")
        self.file_path = file_path


class PageContextShapeError(TypeError):
    """A keyless `@context` returned something other than a mapping.

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


class PageMetadataShapeError(TypeError):
    """A metadata source declared a key or a value the schema does not accept."""

    def __init__(self, source: str, detail: str) -> None:
        """Compose the message from the source and what it got wrong."""
        super().__init__(f"{source} {detail}")
        self.source = source
        self.detail = detail


class PageMetadataConflictError(ValueError):
    """A `page.py` declared its metadata twice, in both forms or in two callables."""

    def __init__(
        self,
        file_path: Path,
        detail: str = "both a metadata dict and an @page.metadata callable",
    ) -> None:
        """Compose the message from the page and the two declarations it carries."""
        super().__init__(f"{file_path} declares {detail}")
        self.file_path = file_path


class PageMetadataRequestError(ValueError):
    """A metadata key set to `True` names the page itself, which needs a request."""

    def __init__(self, key: str) -> None:
        """Compose the message from the key that asked for the page URL."""
        super().__init__(f"`{key}=True` names the page itself and needs a request")
        self.key = key


class PageMetadataTemplateError(ValueError):
    """A title template used a placeholder or a syntax the safe substitution rejects."""

    def __init__(self, template: str, detail: str) -> None:
        """Compose the message from the template and what it got wrong."""
        super().__init__(f"metadata title template {template!r} {detail}")
        self.template = template
        self.detail = detail


__all__ = [
    "PageContextShapeError",
    "PageMetadataConflictError",
    "PageMetadataRequestError",
    "PageMetadataShapeError",
    "PageMetadataTemplateError",
    "PageModuleImportError",
]
