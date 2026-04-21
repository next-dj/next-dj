"""Demonstrate a pluggable `ComponentsBackend` that logs lookups."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.components import FileComponentsBackend


if TYPE_CHECKING:
    from pathlib import Path

    from next.components import ComponentInfo


class CountingFileComponentsBackend(FileComponentsBackend):
    """Extend `FileComponentsBackend` with a per-backend lookup counter."""

    def __init__(self, config: dict[str, object]) -> None:
        """Initialize the file backend and reset the lookup counter."""
        super().__init__(config)
        self.lookup_count = 0

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> ComponentInfo | None:
        """Count each lookup and delegate to the file backend."""
        self.lookup_count += 1
        return super().get_component(name, template_path)
