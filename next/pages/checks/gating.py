"""System checks for the context callables a deferred page boundary gates."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register

from next.checks import NEXT
from next.introspect import callable_name
from next.pages.guards import DEPTH, hidden_guards
from next.pages.loaders import (
    layout_chain,
    load_page_module,
    page_boundaries,
    serves,
    template_source,
)
from next.pages.manager import page

from .contexts import load_routed_pages
from .reads import template_names


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.pages.gating import Plan


def _gated_plans() -> Iterator[tuple[Path, Plan]]:
    """Yield each page a URL serves with a `loading.djx` in its chain, and its split.

    A `page.py` without a body only wraps the pages below it, so it renders nowhere.
    """
    for _url, page_path in load_routed_pages() or []:
        chain = page_boundaries(page_path)
        if chain.deferred and serves(page_path, *load_page_module(page_path)):
            yield page_path, page.gating_plan(page_path, chain)


def _template_reads(page_path: Path, plan: Plan) -> Iterator[CheckMessage]:
    """Yield a warning per layout outside a boundary that reads one of its keys."""
    gated = plan.gated()
    slots = {found.loading: found.slot for found in plan.deferred}
    layouts = layout_chain(page_path)
    steps = plan.graph.steps
    for source, owned in plan.split.jobs:
        keys = gated.keys() & {steps[index].key for index in owned}
        for layout in layouts[slots[source] :]:
            read = template_names(template_source(layout) or "")
            for key in sorted(keys & read):
                yield DjangoWarning(
                    f"{layout} renders outside the deferred page boundary of "
                    f"{page_path} and reads {key!r}, which only a @context callable "
                    "the boundary defers provides, so the layout renders it empty.",
                    hint="Pass the value through a callable of the layout's own "
                    "directory or above, or declare the callable @context(..., "
                    "guard=True) so it runs before the shell.",
                    obj=str(page_path),
                    id="next.W152",
                )


def _loading_reads(page_path: Path, plan: Plan) -> Iterator[CheckMessage]:
    """Yield a warning per `loading.djx` reading a key its job or an inner one gates."""
    gated = plan.gated()
    steps = plan.graph.steps
    levels = {found.loading: found.level for found in plan.deferred}
    for found in plan.deferred:
        loading = cast("Path", found.loading)
        later = {
            steps[index].key
            for source, indices in plan.split.jobs
            if levels[source] <= found.level
            for index in indices
        }
        read = template_names(template_source(loading) or "")
        for key in sorted(gated.keys() & later & read):
            yield DjangoWarning(
                f"{loading} shows before the deferred page boundary of {page_path} "
                f"runs and reads {key!r}, which only a @context callable the "
                "boundary defers provides, so the loading UI renders it empty.",
                hint="Read the value in the page body only, or pass it through a "
                "callable of the boundary's directory or above.",
                obj=str(page_path),
                id="next.W152",
            )


def _metadata_reads(page_path: Path, plan: Plan) -> Iterator[CheckMessage]:
    """Yield a warning per gated callable the page metadata pulls into the shell."""
    steps = plan.graph.steps
    for index, key in plan.split.pulled:
        yield DjangoWarning(
            f"The page metadata of {page_path} reads {key!r}, which only the "
            f"@context callable '{callable_name(steps[index].func)}' provides, so "
            "it runs before the shell rather than in the deferred page boundary.",
            hint="Read the value in the page body only, or accept that the "
            "callable delays the first byte.",
            obj=str(page_path),
            id="next.W152",
        )


@register(Tags.templates, NEXT)
def check_gated_reads(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a read outside a page boundary of a key it gates (`next.W152`)."""
    warnings: list[CheckMessage] = []
    for page_path, plan in _gated_plans():
        warnings.extend(_template_reads(page_path, plan))
        warnings.extend(_loading_reads(page_path, plan))
        warnings.extend(_metadata_reads(page_path, plan))
    return warnings


@register(Tags.templates, NEXT)
def check_hidden_guards(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a gated callable that guards past the scan's depth (`next.W169`)."""
    warnings: list[CheckMessage] = []
    for page_path, plan in _gated_plans():
        steps = plan.graph.steps
        for _source, gated in plan.split.jobs:
            for index in sorted(gated):
                step = steps[index]
                helpers = () if step.guard is not None else hidden_guards(step.func)
                if not helpers:
                    continue
                warnings.append(
                    DjangoWarning(
                        f"The @context callable '{callable_name(step.func)}' of "
                        f"{page_path} runs in a deferred page boundary and calls "
                        f"{', '.join(helpers)}, whose source names a 404, 403 or "
                        f"400 deeper than the {DEPTH} calls the guard scan "
                        "follows, so such an answer would come after the shell.",
                        hint="Declare the callable @context(..., guard=True) to run "
                        "it before the shell, or guard=False to keep it deferred.",
                        obj=str(page_path),
                        id="next.W169",
                    )
                )
    return warnings


__all__ = ["check_gated_reads", "check_hidden_guards"]
