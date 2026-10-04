"""Scripts implementation bound into the `next.ports` slot at app startup."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import override

from django.http import HttpRequest

from next.ports import PageScripts
from next.static import StaticCollector

from .manager import scripts_manager


class PageScriptsImpl(PageScripts):
    """Binds the port to `scripts_manager.render`."""

    @override
    def render(
        self,
        collector: StaticCollector,
        *,
        page_path: Path | None,
        request: HttpRequest | None,
        nonce: Callable[[], str | None],
    ) -> tuple[str, Mapping[str, object]]:
        """Return the head tags and the reserved payload entries of one render."""
        return scripts_manager.render(
            collector, page_path=page_path, request=request, nonce=nonce
        )


__all__ = ["PageScriptsImpl"]
