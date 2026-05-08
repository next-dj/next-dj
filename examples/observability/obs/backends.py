"""Custom backends used by the observability example.

`CountingComponentsBackend` extends the framework filesystem backend
and bumps a counter inside `get_component`. Every successful lookup
contributes one event to the dashboard so the operator can see which
components dominate the page render mix.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from next.components import FileComponentsBackend

from .metrics import incr


if TYPE_CHECKING:
    from next.components.info import ComponentInfo


class CountingComponentsBackend(FileComponentsBackend):
    """`FileComponentsBackend` that counts component resolutions."""

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> "ComponentInfo | None":
        """Return the component info and record one lookup event."""
        info = super().get_component(name, template_path)
        if info is not None:
            incr("components.lookup", name)
        return info
