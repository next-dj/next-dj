"""Demonstrate a pluggable `TemplateLoader` served from an in-memory map."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.pages.loaders import TemplateLoader


if TYPE_CHECKING:
    from pathlib import Path


class InMemoryTemplateLoader(TemplateLoader):
    """Serve template source from a `{path -> source}` mapping."""

    def __init__(self, sources: dict[str, str]) -> None:
        """Store a copy of the mapping to isolate the loader from later edits."""
        self._sources = dict(sources)

    def can_load(self, file_path: Path) -> bool:
        """Return whether `file_path` has an entry in the sources map."""
        return str(file_path) in self._sources

    def load_template(self, file_path: Path) -> str | None:
        """Return the stored source text for `file_path`, or `None`."""
        return self._sources.get(str(file_path))
