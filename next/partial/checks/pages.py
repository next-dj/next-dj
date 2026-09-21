"""One walk of the composed page templates, shared by every zone check of a run.

The walk compiles each page once, so the zone checks share one compile between them.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from django.template import TemplateDoesNotExist, TemplateSyntaxError

from next.checks.common import first_visit, get_router_manager, iter_scanned_page_pairs
from next.conf.signals import settings_reloaded
from next.pages import page


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.base import Template

    from next.urls import RouterBackend, RouterManager


class _ComposedPagesMemo:
    """One walk of the page tree shared by every zone check of a run.

    The manager is held rather than compared by value, so a rebuilt one
    invalidates the pages that were read through the previous one.
    """

    def __init__(self) -> None:
        """Start with no walk on record."""
        self.router_manager: RouterManager | None = None
        self.pages: list[tuple[Path, Template]] = []


_composed_pages = _ComposedPagesMemo()


def iter_composed_pages() -> "Iterator[tuple[Path, Template]]":
    """Yield each page path with its compiled composed template.

    Skips a dynamic `render()` page and a compile failure, both reported by
    `check_composed_templates_compile`, and memoises the walk per router manager.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    if _composed_pages.router_manager is router_manager:
        yield from _composed_pages.pages
        return
    pages = list(_collect_composed_pages(router_manager))
    _composed_pages.router_manager = router_manager
    _composed_pages.pages = pages
    yield from pages


def _collect_composed_pages(
    router_manager: "RouterManager",
) -> "Iterator[tuple[Path, Template]]":
    """Walk every router's scanned pages, de-duplicating by resolved path."""
    seen: set[Path] = set()
    for router in router_manager.backends:
        yield from _iter_router_pages(router, seen)


def reset_composed_pages_memo(**kwargs) -> None:
    """Drop the memoised composed-page list for the next check run.

    Manager identity already invalidates the memo, so this is for a `.djx` edited in
    place under a live manager, which `settings_reloaded` never reports.
    """
    _composed_pages.router_manager = None
    _composed_pages.pages = []


settings_reloaded.connect(reset_composed_pages_memo)


def _iter_router_pages(
    router: "RouterBackend", seen: set[Path]
) -> "Iterator[tuple[Path, Template]]":
    """Yield compiled composed templates for one router's scanned pages."""
    for _url_path, page_path in iter_scanned_page_pairs(router):
        if not first_visit(page_path, seen) or not page.has_template(page_path):
            continue
        try:
            template = page.composed_template_for(page_path)
        except (TemplateSyntaxError, TemplateDoesNotExist, OSError, ValueError):
            continue
        yield page_path, template


__all__ = ["iter_composed_pages", "reset_composed_pages_memo"]
