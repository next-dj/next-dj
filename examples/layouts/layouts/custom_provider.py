"""Demonstrate a pluggable `RegisteredParameterProvider` for layout metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.deps import RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect

    from next.deps import ResolutionContext


class LayoutStampProvider(RegisteredParameterProvider):
    """Inject a constant stamp for parameters named `layout_stamp`."""

    STAMP: str = "bootstrap-5.0"

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Return True when the parameter is named `layout_stamp`."""
        return param.name == "layout_stamp"

    def resolve(self, _param: inspect.Parameter, _context: ResolutionContext) -> object:
        """Return the fixed layout stamp value."""
        return self.STAMP
