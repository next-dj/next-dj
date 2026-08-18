"""Walk of the routed page tree and the serialized context keys it declares.

Both the pages checks and the static reserved-key check read it, and unlike a
`checks` module it stays under the coverage gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.checks.common import first_visit, get_router_manager, iter_scanned_page_pairs

from .loaders import _load_python_module_memo
from .manager import page


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterManager


def iter_existing_scanned_pages(
    router_manager: RouterManager, seen: set[Path]
) -> Iterator[Path]:
    """Yield each existing `page.py` once across routers, de-duplicated by `seen`.

    The identity is the resolved path, so a tree reached through a symlink
    reports once. The spelling the router walked is what travels on, because
    the loader and the page-context registry both key on that spelling.
    Virtual `template.djx`-only pages carry a non-existent path and are skipped.
    """
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            if not first_visit(page_path, seen):
                continue
            if page_path.exists():
                yield page_path


def iter_serialized_page_context_keys() -> Iterator[tuple[Path, str]]:
    """Yield the `page.py` path and key of every keyed `serialize=True` context.

    A keyless `serialize=True` callable spreads the keys of the dict it
    returns at render time, so those keys exist only at runtime and never
    travel through here. One page reached through two spellings yields its
    keys once, under the spelling the registry keys on.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    registry = page._context_manager._context_registry
    for page_path in iter_existing_scanned_pages(router_manager, set()):
        if _load_python_module_memo(page_path) is None:
            continue
        for key, entry in registry.get(page_path, {}).items():
            if entry.serialize and key is not None:
                yield page_path, key


__all__ = ["iter_existing_scanned_pages", "iter_serialized_page_context_keys"]
