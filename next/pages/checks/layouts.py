"""System checks for the page-body placeholder of every `layout.djx`.

The ids are `next.W001` for a layout with no placeholder and `next.W078` for several.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import first_visit, get_router_manager, iter_scanned_page_pairs
from next.pages.placeholder import PLACEHOLDER, PLACEHOLDER_OPEN, placeholder_spans


if TYPE_CHECKING:
    from pathlib import Path


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


def _check_layout_file(layout_file: Path) -> CheckMessage | None:
    """Report a `layout.djx` that carries no placeholder or more than one."""
    try:
        content = layout_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found = len(placeholder_spans(content))
    if found == 0:
        return _missing_placeholder_warning(layout_file)
    if found > 1:
        return _repeated_placeholder_warning(layout_file, found)
    return None


@register(Tags.templates, NEXT)
def check_layout_templates(*args, **kwargs) -> list[CheckMessage]:
    """Check every `layout.djx` for exactly one page-body placeholder."""
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    warnings: list[CheckMessage] = []
    # Nested roots and several routers reach one layout through more than one page.
    seen: set[Path] = set()
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            layout_file = page_path.parent / "layout.djx"
            if not layout_file.exists() or not first_visit(layout_file, seen):
                continue

            warning = _check_layout_file(layout_file)
            if warning:
                warnings.append(warning)

    return warnings


__all__ = ["check_layout_templates"]
