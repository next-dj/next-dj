"""One walk of the composed page templates, shared by every check of a run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.template import TemplateDoesNotExist, TemplateSyntaxError

from next.checks.common import (
    RunMemo,
    first_visit,
    get_router_manager,
    iter_scanned_page_pairs,
)
from next.pages.manager import page


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from django.template.base import Template

    from next.urls import RouterBackend, RouterManager


_composed_pages: RunMemo[tuple[tuple[Path, Template], ...]] = RunMemo()


def iter_composed_pages() -> Iterator[tuple[Path, Template]]:
    """Yield each page path with its compiled composed template, once per run.

    A compile failure is skipped, since `check_composed_templates_compile` names it.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    yield from _composed_pages.get(
        router_manager, lambda: tuple(_collect_composed_pages(router_manager))
    )


def _collect_composed_pages(
    router_manager: RouterManager,
) -> Iterator[tuple[Path, Template]]:
    """Walk every router's scanned pages, de-duplicating by resolved path."""
    seen: set[Path] = set()
    for router in router_manager.backends:
        yield from _iter_router_pages(router, seen)


def _iter_router_pages(
    router: RouterBackend, seen: set[Path]
) -> Iterator[tuple[Path, Template]]:
    """Yield compiled composed templates for one router's scanned pages."""
    for _url_path, page_path in iter_scanned_page_pairs(router):
        if not first_visit(page_path, seen) or not page.has_template(page_path):
            continue
        try:
            template = page.composed_template_for(page_path)
        except (TemplateSyntaxError, TemplateDoesNotExist, OSError, ValueError):
            continue
        yield page_path, template


__all__ = ["iter_composed_pages"]
