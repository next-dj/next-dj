"""Walk of the routed page tree and the serialized context keys it declares.

Both the pages checks and the static reserved-key check read it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.discovery import first_visit, get_router_manager, iter_scanned_page_pairs

from .loaders import _load_python_module_memo
from .manager import page


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterManager


def iter_existing_scanned_page_pairs(
    router_manager: RouterManager, seen: set[Path]
) -> Iterator[tuple[str, Path]]:
    """Yield the routed URL trail and path of each existing `page.py`, once.

    Deduped by resolved path but yielded under the spelling the registry keys on.
    """
    for router in router_manager.backends:
        for url_path, page_path in iter_scanned_page_pairs(router):
            if not first_visit(page_path, seen):
                continue
            if page_path.exists():
                yield url_path, page_path


def iter_existing_scanned_pages(
    router_manager: RouterManager, seen: set[Path]
) -> Iterator[Path]:
    """Yield each existing `page.py` once across routers, de-duplicated by `seen`."""
    for _url_path, page_path in iter_existing_scanned_page_pairs(router_manager, seen):
        yield page_path


def load_scanned_page_modules(router_manager: RouterManager) -> list[tuple[str, Path]]:
    """Execute every existing routed `page.py`, answering the ones that loaded.

    A page-scoped registration exists only once its `page.py` has run.
    """
    return [
        (url_path, page_path)
        for url_path, page_path in iter_existing_scanned_page_pairs(
            router_manager, set()
        )
        if _load_python_module_memo(page_path) is not None
    ]


def iter_serialized_page_context_keys() -> Iterator[tuple[Path, str]]:
    """Yield the `page.py` path and key of every keyed `serialize=True` context.

    A keyless callable spreads its keys only at render time, so those never travel
    through here, and a page reached through two spellings yields its keys once.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    # A page registers only once it runs, so the walk imports before the registry reads.
    pages = load_scanned_page_modules(router_manager)
    serialized = page._context_manager.serialized_keys()
    for _url_path, page_path in pages:
        for key in serialized.get(page_path, ()):
            yield page_path, key


__all__ = [
    "iter_existing_scanned_page_pairs",
    "iter_existing_scanned_pages",
    "iter_serialized_page_context_keys",
    "load_scanned_page_modules",
]
