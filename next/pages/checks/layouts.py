"""System checks for the placeholder, document shell and root marker of layouts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT
from next.checks.common import first_visit, get_router_manager, iter_scanned_page_pairs
from next.components.sources import get_components_manager
from next.pages.loaders import layout_chain, layout_root_scan, template_source
from next.pages.placeholder import (
    PLACEHOLDER,
    PLACEHOLDER_OPEN,
    ROOT_MARKER,
    opens_html_document,
    placeholder_spans,
    scan_root,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterBackend, RouterManager


def _missing_placeholder_warning(layout_file: Path) -> CheckMessage:
    """Build the `next.W001` report for a layout that holds no placeholder."""
    return DjangoWarning(
        f"Layout file {layout_file} carries no {PLACEHOLDER} placeholder, so "
        "composition drops the layout and the pages under it render without its "
        f"markup. Add {PLACEHOLDER} where the page body belongs, or the paired "
        f"{PLACEHOLDER_OPEN} form whose body is the fallback.",
        obj=str(layout_file),
        id="next.W001",
    )


def _repeated_placeholder_warning(layout_file: Path, found: int) -> CheckMessage:
    """Build the `next.W078` report for a layout that holds several placeholders."""
    return DjangoWarning(
        f"Layout file {layout_file} carries {found} {PLACEHOLDER} placeholders. "
        "Composition fills the first one and every other renders its own fallback "
        "instead of the page, so keep exactly one.",
        obj=str(layout_file),
        id="next.W078",
    )


def _misplaced_marker_error(layout_file: Path) -> CheckMessage:
    """Build the `next.E168` report for a root marker the layout does not honor."""
    return Error(
        f"Layout file {layout_file} carries a {ROOT_MARKER} marker that is not the "
        "first tag after the optional load tags and comments, or is repeated. "
        "Composition ignores it, so the layout is no root. Keep one marker and "
        "move it to the top of the file.",
        obj=str(layout_file),
        id="next.E168",
    )


def _nested_document_warning(layout_file: Path, *, misplaced: bool) -> CheckMessage:
    """Build the `next.W133` report for a layout shell nested in an outer layout."""
    reason = (
        f" Its {ROOT_MARKER} marker is ignored, see next.E168." if misplaced else ""
    )
    return DjangoWarning(
        f"Layout file {layout_file} writes an <html> element while a layout further "
        "out wraps it, so the page renders two documents. Open the layout with "
        f"{ROOT_MARKER} to make it the root of its chain.{reason}",
        obj=str(layout_file),
        id="next.W133",
    )


def _stray_marker_warning(source: Path, kind: str) -> CheckMessage:
    """Build the `next.W134` report for a marker outside any `layout.djx`."""
    return DjangoWarning(
        f"{kind} {source} carries a {ROOT_MARKER} marker, which only a layout.djx "
        "reads, so it has no effect. Move it to the layout that owns the document "
        "shell.",
        obj=str(source),
        id="next.W134",
    )


def _check_layout_file(layout_file: Path) -> list[CheckMessage]:
    """Report a `layout.djx` placeholder, root marker and nested document mistakes."""
    content = template_source(layout_file)
    root = layout_root_scan(layout_file)
    if content is None or root is None:
        return []
    messages: list[CheckMessage] = []
    found = len(placeholder_spans(content))
    if found == 0:
        messages.append(_missing_placeholder_warning(layout_file))
    elif found > 1:
        messages.append(_repeated_placeholder_warning(layout_file, found))
    misplaced = bool(root.markers) and not root.is_root
    if misplaced:
        messages.append(_misplaced_marker_error(layout_file))
    if (
        not root.is_root
        and opens_html_document(content)
        and len(layout_chain(layout_file)) > 1
    ):
        messages.append(_nested_document_warning(layout_file, misplaced=misplaced))
    return messages


def _scanned_pages(router_manager: RouterManager) -> Iterator[Path]:
    """Yield the `page.py` of every page every router scans."""
    for router in router_manager.backends:
        yield from _router_pages(router)


def _router_pages(router: RouterBackend) -> Iterator[Path]:
    """Yield the `page.py` of every page one router scans."""
    for _url_path, page_path in iter_scanned_page_pairs(router):
        yield page_path


def iter_layout_ancestry(router_manager: RouterManager) -> Iterator[Path]:
    """Yield once each layout composition wraps a scanned page in, a root ending it.

    That covers layouts of folders without a page, `DIRS` roots and any above the tree.
    """
    seen: set[Path] = set()
    for page_path in _scanned_pages(router_manager):
        for layout_file in layout_chain(page_path):
            if first_visit(layout_file, seen):
                yield layout_file


@register(Tags.templates, NEXT)
def check_layout_templates(*args, **kwargs) -> list[CheckMessage]:
    """Check every `layout.djx` for its placeholder, root marker and document shell."""
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []

    messages: list[CheckMessage] = []
    for layout_file in iter_layout_ancestry(router_manager):
        messages.extend(_check_layout_file(layout_file))
    return messages


def _stray_markers(router_manager: RouterManager) -> Iterator[CheckMessage]:
    """Yield a report for each page and component template that holds a marker."""
    seen: set[Path] = set()
    for page_path in _scanned_pages(router_manager):
        template = page_path.parent / "template.djx"
        if template.exists() and first_visit(template, seen):
            yield from _stray_marker_report(template, "Page template")
    for backend in get_components_manager().backends:
        for info in backend.iter_components():
            source = info.template_path
            if source is not None and source.exists() and first_visit(source, seen):
                yield from _stray_marker_report(source, "Component template")


def _stray_marker_report(source: Path, kind: str) -> Iterator[CheckMessage]:
    """Yield the `next.W134` report when `source` holds a root marker anywhere."""
    content = template_source(source)
    if content is not None and scan_root(content).markers:
        yield _stray_marker_warning(source, kind)


@register(Tags.templates, NEXT)
def check_root_marker_outside_layouts(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a root marker in a `template.djx` or component template."""
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []
    return list(_stray_markers(router_manager))


__all__ = [
    "check_layout_templates",
    "check_root_marker_outside_layouts",
    "iter_layout_ancestry",
]
