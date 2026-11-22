"""System checks for the `{% more %}` tags and the pagers of a composed page."""

import inspect
from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

from django.core.checks import CheckMessage, Error, Tags, register

from next.checks import NEXT
from next.pages import page
from next.pages.checks.composed import iter_composed_pages
from next.partial.more import MoreNode
from next.partial.paging import Pager, pager_of

from .codes import E_MORE_WITHOUT_WINDOW, E_MORE_ZONE_UNKNOWN, E_PAGER_PARAM_SHARED
from .nodes import zone_nodes


if TYPE_CHECKING:
    from pathlib import Path

    from django.template.base import Template


def _more_nodes(template: "Template") -> list[MoreNode]:
    """Return every `{% more %}` node of a compiled template."""
    return cast("list[MoreNode]", template.nodelist.get_nodes_by_type(MoreNode))


@register(Tags.templates, NEXT)
def check_more_zone_exists(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Error when a `{% more %}` names a zone its page lacks (`next.E220`)."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        declared = {node.name for node in zone_nodes(template)}
        messages.extend(
            Error(
                f'{{% more %}} in {page_path} grows the zone "{node.options.zone}", '
                "which the composed page template does not declare. Every batch it "
                "fetches lands nowhere.",
                hint="Name the {% zone %} that holds the rows in zone=.",
                obj=str(page_path),
                id=E_MORE_ZONE_UNKNOWN,
            )
            for node in _more_nodes(template)
            if node.options.zone not in declared
        )
    return messages


def _pagers(page_path: "Path") -> Iterator[Pager]:
    """Yield the pager of every `Paging` parameter a render of `page_path` fills."""
    for _key, entry in page.contexts.run_order(page_path):
        for param in inspect.signature(entry.func).parameters.values():
            pager = pager_of(param.annotation)
            if pager is not None:
                yield pager


@register(Tags.templates, NEXT)
def check_more_has_page_window(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Error when `prune=` or `url=` sits on a page no pager serves (`next.E235`).

    Both restore a window from `?<param>=<token>&restore=1`, which only a pager reads.
    """
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        windowed = [node for node in _more_nodes(template) if node.options.windowed]
        if not windowed or next(_pagers(page_path), None) is not None:
            continue
        messages.extend(
            Error(
                f'{{% more %}} in {page_path} grows the zone "{node.options.zone}" '
                "with prune= or url=, yet no context callable of the page takes "
                "Annotated[Paging, <Pager>]. A reload of the written restore URL "
                "renders one batch instead of the window.",
                hint=(
                    "Take page: Annotated[Paging, pager] in the callable that pages "
                    "the rows and pass the Window it returns to next=."
                ),
                obj=str(page_path),
                id=E_MORE_WITHOUT_WINDOW,
            )
            for node in windowed
        )
    return messages


@register(Tags.templates, NEXT)
def check_pager_params_unique(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Error when two pagers of one page read the same query parameter (`next.E236`).

    Each list would then read the token of the other, and a restore would mix them.
    """
    messages: list[CheckMessage] = []
    for page_path, _template in iter_composed_pages():
        readers: dict[str, list[Pager]] = {}
        for pager in _pagers(page_path):
            found = readers.setdefault(pager.param, [])
            if all(pager is not other for other in found):
                found.append(pager)
        messages.extend(
            Error(
                f"{len(pagers)} pagers of {page_path} read the query parameter "
                f'"{param}", so each list would page by the token of the other.',
                hint="Give each pager its own param=.",
                obj=str(page_path),
                id=E_PAGER_PARAM_SHARED,
            )
            for param, pagers in readers.items()
            if len(pagers) > 1
        )
    return messages


__all__ = [
    "check_more_has_page_window",
    "check_more_zone_exists",
    "check_pager_params_unique",
]
