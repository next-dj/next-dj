"""System checks for the boundary a `page.py` or a `component.py` declares."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from django.core.checks import Tags, Warning as DjangoWarning

from next.checks import NEXT, register
from next.checks.common import DeclaringCall, misplaced_error, misplaced_errors
from next.components.defer import COMPONENT_MODULE, defer_declarations
from next.pages.checks.access import load_declarations
from next.pages.checks.composed import iter_composed_pages
from next.pages.defer import defer_declaration, page_defer
from next.pages.loaders import PAGE_MODULE
from next.pages.nodes import PageBoundaryNode


if TYPE_CHECKING:
    from django.core.checks import CheckMessage
    from django.template import Template


_CODE: Final = "next.E222"
_DEFER: Final = DeclaringCall("page.defer()", _CODE, PAGE_MODULE, "page")


@register(NEXT)
def check_boundary_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a page or component boundary call made off its module (`next.E222`)."""
    load_declarations()
    errors = misplaced_errors(_DEFER, defer_declaration.outside())
    errors.extend(
        misplaced_error(
            DeclaringCall(call, _CODE, COMPONENT_MODULE, "component"), file_path
        )
        for file_path, call in defer_declarations.outside()
    )
    return errors


def _defers(template: Template) -> bool:
    """Whether a `loading.djx` defers the page `template` composes."""
    nodes = cast(
        "list[PageBoundaryNode]", template.nodelist.get_nodes_by_type(PageBoundaryNode)
    )
    return any(node.loading is not None for node in nodes)


@register(Tags.templates, NEXT)
def check_idle_page_defer(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a `page.defer()` of a page no `loading.djx` defers (`next.W190`)."""
    return [
        DjangoWarning(
            f"page.defer() in {page_path} tunes the job of a page boundary, and no "
            "loading.djx in the directory of the page or above it defers the page, so "
            "the call changes nothing.",
            hint="Add a loading.djx to the directory of the page or a directory above "
            "it, or remove the page.defer() call.",
            obj=str(page_path),
            id="next.W190",
        )
        for page_path, template in iter_composed_pages()
        if page_defer(page_path) is not None and not _defers(template)
    ]


__all__ = ["check_boundary_declarations", "check_idle_page_defer"]
