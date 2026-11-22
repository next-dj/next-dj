"""System checks for the cache and the `page.router()` call of a routed page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import DeclaringCall, declaration_errors
from next.conf.router import router_enabled
from next.pages.loaders import PAGE_MODULE, load_python_module_memo
from next.pages.responses import cache_control
from next.pages.router import router_store
from next.pages.watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _page_modules() -> Iterator[Path]:
    """Yield every `page.py` of the page trees."""
    for root in get_pages_directories_for_watch():
        yield from sorted(root.rglob(PAGE_MODULE))


@register(NEXT)
def check_no_store_pages(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a page whose cache forbids every store (`next.W178`)."""
    if not router_enabled():
        return []
    warnings: list[CheckMessage] = []
    for file_path in _page_modules():
        module = load_python_module_memo(file_path)
        control = cache_control(getattr(module, "cache", None))
        if control is not None and not control.stores:
            warnings.append(
                DjangoWarning(
                    f"{file_path} declares Cache-Control: no-store, so the browser "
                    "keeps no back-forward copy of the page the client router "
                    "restores from.",
                    hint="Prefer no_cache, which revalidates and keeps the copy.",
                    obj=str(file_path),
                    id="next.W178",
                )
            )
    return warnings


_ROUTER_CALL: Final = DeclaringCall("page.router()", "next.E238", PAGE_MODULE, "page")


@register(NEXT)
def check_router_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `page.router()` call that applies nothing (`next.E238`)."""
    return declaration_errors(
        _ROUTER_CALL,
        router_store,
        consequence="The page keeps the default router options.",
        hint="Call page.router(navigate=..., prefetch=..., snapshot=...) with bools "
        "at the top of the module.",
    )


__all__ = ["check_no_store_pages", "check_router_declarations"]
