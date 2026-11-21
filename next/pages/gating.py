"""How a page with a `loading.djx` splits its context callables into shell and jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any, Final, cast

from django.conf import settings

from next.introspect import callable_name
from next.ports import run_bridged

from .graph import Blame, Phase, arun_phase, closure, merged, run_phase
from .guards import scans_as_guard
from .scope import render_scope


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.static.serializers import JsContextSerializer

    from .graph import ContextGraph, Step
    from .loaders import BoundaryChain, PageBoundary


logger = logging.getLogger(__name__)

GATE_KEY: Final = "_next_page_gate"
"""Context key of the `Gate` a gated shell hands its page boundary jobs."""

SLOW_SHELL: Final = 0.2
"""Seconds past which a `DEBUG` shell of a deferred page names its slowest callable."""


@dataclass(frozen=True, slots=True)
class Split:
    """The steps the shell runs and those each page boundary job runs, by `loading.djx`.

    `guards`, `pulled` and `promoted` name the gated steps the shell took, and why.
    """

    shell: frozenset[int]
    jobs: tuple[tuple[Path, frozenset[int]], ...]
    guards: tuple[int, ...] = ()
    pulled: tuple[tuple[int, str], ...] = ()
    promoted: tuple[int, ...] = ()

    def job(self, loading: Path) -> frozenset[int]:
        """Return the steps the job of the `loading.djx` at `loading` runs."""
        return next((steps for source, steps in self.jobs if source == loading), _NONE)


_NONE: Final[frozenset[int]] = frozenset()


@dataclass(frozen=True, slots=True)
class Plan:
    """The split of one page's context graph across its deferred page boundaries.

    `levels` holds the directory each step feeds, `-1` for the page's own body.
    """

    graph: ContextGraph
    levels: tuple[int, ...]
    deferred: tuple[PageBoundary, ...]
    split: Split
    inherited: tuple[int, ...] = ()

    def awaits(self, steps: Iterable[int]) -> bool:
        """Return whether one of `steps` is an async callable."""
        graph = self.graph
        return any(graph.steps[index].is_async for index in steps)

    def gated(self) -> dict[str, tuple[int, ...]]:
        """Return each key only gated steps provide, with the steps providing it."""
        provided: dict[str, list[int]] = {}
        for index, step in enumerate(self.graph.steps):
            if step.key is not None:
                provided.setdefault(step.key, []).append(index)
        shell = self.split.shell
        return {
            key: tuple(steps)
            for key, steps in provided.items()
            if not any(index in shell for index in steps)
        }

    def failed_level(self, blamed: Blame) -> int:
        """Return the directory of the group `blamed` names, `-1` for the page's own."""
        return blamed.level(self.inherited)


def _owner(level: int, deferred: tuple[PageBoundary, ...]) -> Path | None:
    """Return the `loading.djx` whose job runs a step of `level`, `None` for the shell.

    It is the innermost boundary above the directory of the step.
    """
    return next((found.loading for found in deferred if found.level > level), None)


def _level(step: Step, inherited: tuple[int, ...]) -> int:
    """Return the directory a step feeds, its own layout for an inheritable one.

    An own `inherit_context` callable feeds the layout of the page's directory first.
    """
    if step.group >= 0:
        return inherited[step.group]
    return 0 if step.inherits else -1


def step_levels(graph: ContextGraph, inherited: tuple[int, ...]) -> tuple[int, ...]:
    """Return the directory each step of `graph` feeds, `-1` for the page's own body."""
    return tuple(_level(step, inherited) for step in graph.steps)


def _guarding(graph: ContextGraph, index: int) -> bool:
    step = graph.steps[index]
    return step.guard if step.guard is not None else scans_as_guard(step.func)


def _prefix(last: int) -> frozenset[int]:
    """Return every step up to `last`, what a sequential run has run before it ends."""
    return frozenset(range(last + 1))


def guard_steps(graph: ContextGraph) -> frozenset[int]:
    """Return the steps a shell runs for the guards of `graph`, each one up to the last.

    A boundary reload runs them before its component, as if every step were gated.
    """
    guards = [index for index in range(len(graph.steps)) if _guarding(graph, index)]
    return _prefix(guards[-1]) if guards else _NONE


def _pulled(
    graph: ContextGraph, metadata: frozenset[str], shell: set[int]
) -> list[tuple[int, str]]:
    """Return the gated steps the metadata reads, a keyless one where no key provides.

    A keyless result is positional, so the shell takes every step before it too.
    """
    steps = graph.steps
    pulled: list[tuple[int, str]] = []
    for key in sorted(metadata):
        keyed = [index for index, step in enumerate(steps) if step.key == key]
        if keyed:
            gated = [index for index in keyed if index not in shell]
            pulled.extend((index, key) for index in gated)
            shell |= closure(graph, frozenset(gated))
            continue
        keyless = [
            index
            for index, step in enumerate(steps)
            if step.key is None and index not in shell
        ]
        if keyless:
            pulled.extend((index, key) for index in keyless)
            shell |= _prefix(keyless[-1])
    return pulled


def plan_gating(
    graph: ContextGraph,
    inherited: tuple[int, ...],
    deferred: tuple[PageBoundary, ...],
    metadata: frozenset[str],
    file_path: Path,
) -> Plan:
    """Split `graph` between the shell and the jobs of `deferred`, nearest first.

    A guard moves to the shell with every step before it, as a run without gating has.
    """
    levels = step_levels(graph, inherited)
    owners = tuple(_owner(level, deferred) for level in levels)
    shell = {index for index, owner in enumerate(owners) if owner is None}
    gated = frozenset(range(len(owners))) - shell
    guards = tuple(index for index in sorted(gated) if _guarding(graph, index))
    if guards:
        shell |= _prefix(guards[-1])
    pulled = _pulled(graph, metadata, shell)
    jobs: list[tuple[Path, frozenset[int]]] = []
    for found in deferred:
        loading = cast("Path", found.loading)
        owned = (index for index, owner in enumerate(owners) if owner == loading)
        jobs.append((loading, frozenset(owned) - shell))
    promoted = tuple(sorted(gated & shell))
    split = Split(frozenset(shell), tuple(jobs), guards, tuple(pulled), promoted)
    if settings.DEBUG:
        _note_promoted(graph, split, file_path)
    return Plan(graph, levels, deferred, split, inherited)


def _note_promoted(graph: ContextGraph, split: Split, file_path: Path) -> None:
    """Log at `DEBUG` each gated callable the shell runs, and why."""
    steps = graph.steps
    for index in split.guards:
        logger.debug(
            "The @context callable %r of %s can answer 404, 403 or 400, so it runs "
            "before the shell of its deferred page boundary.",
            callable_name(steps[index].func),
            file_path,
        )
    for index, key in split.pulled:
        logger.debug(
            "The @context callable %r of %s provides %r, which page metadata reads, "
            "so it runs before the shell of its deferred page boundary.",
            callable_name(steps[index].func),
            file_path,
            key,
        )
    if split.promoted:
        logger.debug(
            "The shell of %s runs the gated @context callables %s, a guard or the "
            "page metadata needing them or every callable before them.",
            file_path,
            ", ".join(
                repr(callable_name(steps[index].func)) for index in split.promoted
            ),
        )


def note_slow(
    plan: Plan, file_path: Path, timings: Mapping[int, float] | None, elapsed: float
) -> None:
    """Log at `DEBUG` the slowest callable of a shell that took past `SLOW_SHELL`."""
    if timings is None or not timings or elapsed <= SLOW_SHELL:
        return
    index = max(timings, key=timings.__getitem__)
    level = plan.levels[index]
    own = any(found.level == level for found in plan.deferred)
    logger.debug(
        "The shell of %s took %.0f ms, %.0f ms of it in the @context callable %r, "
        "which %s.",
        file_path,
        elapsed * 1000,
        timings[index] * 1000,
        callable_name(plan.graph.steps[index].func),
        "belongs to the directory of a loading.djx, so its layout is not deferred, "
        "move the slow work into the page or a component"
        if own
        else "sits above every loading.djx",
    )


@dataclass(frozen=True, slots=True)
class Advanced:
    """What one page boundary job adds to its context, and the values it publishes."""

    layer: dict[str, object]
    published: dict[str, object]
    serializers: Mapping[str, JsContextSerializer]


@dataclass(frozen=True, slots=True)
class Gate:
    """The gated callables of one page render, with the results the earlier parts took.

    The shell puts it in the context, and each page boundary job runs its own steps.
    """

    plan: Plan
    chain: BoundaryChain
    file_path: Path
    request: HttpRequest | None
    url_kwargs: Mapping[str, object]
    dep_cache: dict[str, Any]
    results: Mapping[int, object]

    def level(self, loading: Path) -> int | None:
        """Return the directory level of the boundary of `loading`, `None` for none."""
        return next(
            (found.level for found in self.plan.deferred if found.loading == loading),
            None,
        )

    def inside(self, loading: Path, level: int) -> bool:
        """Return whether a directory `level` up sits inside the job of `loading`."""
        return any(
            found.level > level
            for found in self.plan.deferred
            if found.loading == loading
        )

    def blame(self, failed: Blame | None = None) -> Blame:
        """Return the inherited results the gate took, then what `failed` settled.

        A page boundary cuts its error UI context from it, as an inline render does.
        """
        held = Blame()
        steps = self.plan.graph.steps
        results = self.results
        for index in sorted(results):
            step = steps[index]
            if step.group >= 0:
                held.settle(step.group, step.key, results[index])
        if failed is not None:
            held.settled.extend(failed.settled)
        return held

    def advance(self, loading: Path) -> Advanced:
        """Run the steps the job of `loading` owns and return what they add.

        An async step runs the steps through the graph, in a request scope of its own.
        """
        selected = self.plan.split.job(loading)
        if not selected:
            return Advanced({}, {}, {})
        phase = Phase(selected, self.results)
        if self.plan.awaits(selected):
            taken = run_bridged(partial(self._scoped, phase))
        else:
            taken = run_phase(
                self.plan.graph,
                self.file_path,
                phase,
                request=self.request,
                url_kwargs=self.url_kwargs,
                dep_cache=self.dep_cache,
            )
        results = {**self.results, **taken}
        graph = self.plan.graph
        before = merged(graph, self.file_path, self.results).js_context
        after = merged(graph, self.file_path, results)
        layer: dict[str, object] = dict(after.context_data)
        layer[GATE_KEY] = replace(self, results=results)
        published = {
            key: value for key, value in after.js_context.items() if key not in before
        }
        return Advanced(layer, published, after.js_context_serializers)

    async def _scoped(self, phase: Phase) -> dict[int, object]:
        async with render_scope(self.dep_cache):
            return await arun_phase(
                self.plan.graph,
                self.file_path,
                phase,
                request=self.request,
                url_kwargs=self.url_kwargs,
                dep_cache=self.dep_cache,
            )


__all__ = [
    "GATE_KEY",
    "SLOW_SHELL",
    "Advanced",
    "Gate",
    "Plan",
    "Split",
    "guard_steps",
    "note_slow",
    "plan_gating",
    "step_levels",
]
