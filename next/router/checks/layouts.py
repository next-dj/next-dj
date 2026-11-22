"""System checks for the layout slots and the page keys a layout chrome reads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.conf.router import router_enabled
from next.pages.checks.contexts import load_routed_pages
from next.pages.checks.reads import chrome_names
from next.pages.gating import step_levels
from next.pages.loaders import (
    layout_chain,
    load_page_module,
    serves,
    shared_layouts,
    template_source,
)
from next.pages.manager import page
from next.pages.placeholder import placeholder_spans, slot_problem
from next.pages.watch import get_layout_djx_paths_for_watch
from next.utils import is_within


if TYPE_CHECKING:
    from pathlib import Path


def layouts() -> list[Path]:
    """Return every `layout.djx` of the page trees and the `DIRS` roots, sorted."""
    return sorted({*get_layout_djx_paths_for_watch(), *shared_layouts()})


@register(NEXT)
def check_layout_slots(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a layout placeholder the slot markers cannot wrap (`next.E239`)."""
    if not router_enabled():
        return []
    errors: list[CheckMessage] = []
    for layout in layouts():
        text = template_source(layout)
        problem = None if text is None else slot_problem(text)
        if problem is not None:
            errors.append(
                Error(
                    f"The {{% template %}} placeholder of {layout} sits {problem}, "
                    "where the client router cannot mark the slot, so every "
                    "navigation under it loads hard or morphs the whole body.",
                    hint="Move the placeholder into the body of an element.",
                    obj=str(layout),
                    id="next.E239",
                )
            )
    return errors


def _distance(layout: Path, page_path: Path) -> int | None:
    """Return how many directories `layout` sits above the page, `None` off its path."""
    folder = layout.parent
    start = page_path.parent
    if not is_within(start, folder):
        return None
    return len(start.parts) - len(folder.parts)


def _varying_keys(page_path: Path, distance: int | None) -> frozenset[str]:
    """Return the keys a page takes from callables below a layout `distance` up.

    A callable of the layout's own directory or above feeds every page below it alike.
    """
    contexts = page.contexts
    graph = contexts.graph(page_path)
    levels = step_levels(graph, contexts.inherited_levels(page_path))
    return frozenset(
        step.key
        for step, level in zip(graph.steps, levels, strict=True)
        if step.key is not None and (distance is None or level < distance)
    )


def _kept_names(layout: Path) -> frozenset[str]:
    """Return what `layout` reads outside its zones and slot, none without a slot."""
    text = template_source(layout)
    if text is None or not placeholder_spans(text):
        return frozenset()
    return chrome_names(text)


def _page_reads() -> dict[Path, dict[str, Path]]:
    """Map each layout to the page keys its chrome reads, with a page that sets each."""
    kept: dict[Path, frozenset[str]] = {}
    found: dict[Path, dict[str, Path]] = {}
    for _url, page_path in load_routed_pages() or []:
        if not serves(page_path, *load_page_module(page_path)):
            continue
        for layout in layout_chain(page_path):
            names = kept.get(layout)
            if names is None:
                names = kept[layout] = _kept_names(layout)
            if not names:
                continue
            keys = names & _varying_keys(page_path, _distance(layout, page_path))
            for key in sorted(keys):
                found.setdefault(layout, {}).setdefault(key, page_path)
    return found


@register(NEXT)
def check_layout_page_reads(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a layout chrome reading a key the pages below set (`next.W183`)."""
    if not router_enabled():
        return []
    warnings: list[CheckMessage] = []
    for layout, keys in sorted(_page_reads().items()):
        shown = ", ".join(repr(key) for key in sorted(keys))
        first = min(keys)
        warnings.append(
            DjangoWarning(
                f"The layout {layout} reads {shown} outside a zone, which a @context "
                f"callable of a page below it provides, as {keys[first]} does for "
                f"{first!r}, so its chrome differs between pages that set it "
                "differently and the client router cannot keep the layout across "
                "a navigation between them.",
                hint="Wrap the part that reads it in a {% zone %}, which a kept "
                "layout morphs on its own, or move it into the page slot.",
                obj=str(layout),
                id="next.W183",
            )
        )
    return warnings


__all__ = ["check_layout_page_reads", "check_layout_slots", "layouts"]
