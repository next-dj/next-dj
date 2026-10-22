"""The system check for a breadcrumb a route group `page.py` declares in vain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.checks import CheckMessage, Warning as DjangoWarning, register

from next.checks import NEXT
from next.pages.loaders import in_page_tree
from next.utils import is_route_group

from .pages import loaded_metadata_pages


if TYPE_CHECKING:
    from next.pages.metadata.markers import Segment


def declares_crumb(segment: Segment | None) -> bool:
    """Whether a static metadata segment names a breadcrumb of its own."""
    if segment is None:
        return False
    label = segment.breadcrumb
    return label is not None and label is not False


@register(NEXT)
def check_route_group_breadcrumbs(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a breadcrumb declared on a route group `page.py` (`next.W136`).

    A group serves the URL of its parent and gives no crumb, so the label is dropped.
    """
    warnings: list[CheckMessage] = []
    for entry in loaded_metadata_pages():
        directory = entry.page_path.parent
        if not is_route_group(directory.name) or not in_page_tree(directory.parent):
            continue
        if not declares_crumb(entry.segment):
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} declares a breadcrumb, but {directory.name!r} is "
                "a route group, which serves the URL of its parent and gives no "
                "crumb, so the breadcrumb is dropped.",
                hint=(
                    "Declare the breadcrumb in the page.py of the parent directory, "
                    "or of a routed directory inside the group."
                ),
                obj=str(entry.page_path),
                id="next.W136",
            )
        )
    return warnings


__all__ = ["check_route_group_breadcrumbs", "declares_crumb"]
