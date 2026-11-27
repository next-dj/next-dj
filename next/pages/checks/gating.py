"""System checks for the context callables a page boundary or a client trip needs."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning
from django.template import Template, TemplateSyntaxError

from next.checks import NEXT, register
from next.components.defer import defer_declarations
from next.components.nodes import ComponentTagNode
from next.components.sources import get_components_manager
from next.components.trip import client_mode
from next.deps.resolver import current_resolver
from next.introspect import callable_name
from next.pages.gating import answers_by_contract
from next.pages.guards import hidden_guards, scans_as_guard
from next.pages.loaders import (
    layout_chain,
    load_page_module,
    page_boundaries,
    serves,
    template_source,
)
from next.pages.manager import page

from .composed import iter_composed_pages
from .contexts import load_routed_pages
from .reads import template_names


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django.template.base import Node

    from next.boundaries.options import DeferMode
    from next.components.info import ComponentInfo
    from next.pages.gating import SplitPlan


def _gated_plans() -> Iterator[tuple[Path, SplitPlan]]:
    """Yield each page a URL serves with a `loading.djx` in its chain, and its split.

    A `page.py` without a body only wraps the pages below it, so it renders nowhere.
    """
    for _url, page_path in load_routed_pages() or []:
        chain = page_boundaries(page_path)
        if chain.deferred and serves(page_path, *load_page_module(page_path)):
            yield page_path, page.gating_plan(page_path, chain)


def _template_reads(page_path: Path, plan: SplitPlan) -> Iterator[CheckMessage]:
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


def _loading_reads(page_path: Path, plan: SplitPlan) -> Iterator[CheckMessage]:
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


def _metadata_reads(page_path: Path, plan: SplitPlan) -> Iterator[CheckMessage]:
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


def _answer(func: Callable[..., object]) -> str | None:
    """Return how the guard scan sees `func` answer a status, `None` for not at all."""
    if scans_as_guard(func):
        return "answers 404, 403 or 400"
    hidden = hidden_guards(func)
    if hidden:
        return f"calls {', '.join(hidden)}, whose source names 404, 403 or 400"
    return None


_HINT = (
    "Declare the callable @context(..., guard=True) so it runs before the shell and "
    "before every boundary reload, or move the status check into a callable of its "
    "own declared so."
)


def _hidden(
    page_path: Path, func: Callable[..., object], answer: str, why: str
) -> CheckMessage:
    return DjangoWarning(
        f"The @context callable '{callable_name(func)}' of {page_path} is not "
        f"declared guard=True and {answer}, so {why}.",
        hint=_HINT,
        obj=str(page_path),
        id="next.W169",
    )


_STREAMED = "a streamed page shows such an answer as an error UI under a 200 status"
_RELOADED = (
    "a reload of a client-trip boundary of the page by its token never runs it and "
    "an access check in it does not hold there"
)


def _mode(node: ComponentTagNode, info: ComponentInfo) -> DeferMode | None:
    """Return the mode a call runs under, the call site over the `component.py`."""
    written = getattr(node, "options", None)
    mode: DeferMode | None = getattr(written, "mode", None)
    if mode is not None:
        return mode
    options = defer_declarations.options(info.module_path)
    return None if options is None else options.mode


def _nodes(template: Template) -> Iterator[Node]:
    return iter(template.nodelist.get_nodes_by_type(ComponentTagNode))


def _compiled(path: Path) -> Template | None:
    """Return the compiled template at `path`, `None` for one that fails."""
    try:
        return Template(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, TemplateSyntaxError):
        return None


def _trips(template: Template, anchor: Path, seen: set[Path]) -> bool:
    """Return whether `template` or a component it renders makes a client-trip call."""
    for node in _nodes(template):
        call = cast("ComponentTagNode", node)
        info = get_components_manager().get_component(call.name, anchor)
        if info is None:
            continue
        if client_mode(_mode(call, info)):
            return True
        source = info.template_path
        if source is None or source in seen:
            continue
        seen.add(source)
        inner = _compiled(source)
        if inner is not None and _trips(inner, source, seen):
            return True
    return False


def _deferred_answers(page_path: Path, plan: SplitPlan) -> Iterator[CheckMessage]:
    """Yield a warning per callable a page boundary job runs that answers a status."""
    steps = plan.graph.steps
    for _source, gated in plan.split.jobs:
        for index in sorted(gated):
            func = steps[index].func
            answer = _answer(func)
            if answer is not None:
                yield _hidden(page_path, func, answer, _STREAMED)


def _reloaded_answers(page_path: Path) -> Iterator[CheckMessage]:
    """Yield a warning per unguarded callable answering a status on a trip page."""
    resolver = current_resolver()
    for step in page.contexts.graph(page_path).steps:
        if step.guard or answers_by_contract(step.func, resolver):
            continue
        answer = _answer(step.func)
        if answer is not None:
            yield _hidden(page_path, step.func, answer, _RELOADED)


@register(Tags.templates, NEXT)
def check_hidden_guards(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about an unguarded callable the scan sees answer a status (`next.W169`).

    It runs after the first byte under a `loading.djx`, and never on a client trip.
    """
    warnings: list[CheckMessage] = []
    for page_path, plan in _gated_plans():
        warnings.extend(_deferred_answers(page_path, plan))
    for page_path, template in iter_composed_pages():
        if _trips(template, page_path, set()):
            warnings.extend(_reloaded_answers(page_path))
    return warnings


__all__ = ["check_gated_reads", "check_hidden_guards"]
