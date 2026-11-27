"""How a page with a `loading.djx` builds its shell context, its guards first."""

from __future__ import annotations

import time
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

from django.conf import settings

from next.deps.resolver import current_resolver
from next.deps.scope import bridged_scope
from next.pages.boundaries import Caught, ErrorContext
from next.pages.gating import GATE_KEY, ShellGate, SplitPlan, note_slow, plan_gating
from next.pages.graph import (
    Phase,
    arun_phase,
    blaming,
    callable_keys,
    merged,
    run_phase,
)
from next.pages.metadata.chain import FoldedMetadata, contained_chain
from next.seeding import METADATA_KEY

from .render import ContextRender


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.caches import PageCache
    from next.pages.loaders import BoundaryChain


class PageGating(ContextRender):
    """Split the callables of a page across its boundaries and build its shell."""

    _plans: PageCache[Path, tuple[object, int, object, SplitPlan]]

    def gating_plan(self, file_path: Path, chain: BoundaryChain) -> SplitPlan:
        """Return the split of the callables of `file_path` across its page boundaries.

        It is computed once per graph, registry epoch and set of `loading.djx` files.
        """
        graph = self._context_manager.graph(file_path)
        epoch = self.epoch
        deferred = chain.deferred
        held = self._plans.get(file_path)
        if (
            held is not None
            and held[0] is graph
            and held[1] == epoch
            and held[2] == deferred
        ):
            return held[3]
        resolver = current_resolver()
        entry = contained_chain(self._metadata_registry, file_path)
        metadata = frozenset[str]().union(
            *(
                callable_keys(source.func, resolver)
                for source in entry.tail
                if source.func is not None
            )
        )
        plan = plan_gating(
            graph,
            self._context_manager.inherited_levels(file_path),
            deferred,
            metadata,
            file_path,
        )
        self._plans[file_path] = (graph, epoch, deferred, plan)
        return plan

    def gated_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain,
    ) -> dict[str, object] | Caught:
        """Build the shell context of a page with a `loading.djx`, its guards first.

        The gated callables wait in the `ShellGate` for the jobs of the page boundaries.
        """
        plan = self.gating_plan(file_path, chain)
        if plan.awaits(plan.split.shell) or self._metadata_awaits(file_path):
            return bridged_scope(
                dep_cache,
                partial(
                    self.agated_context,
                    file_path,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                    chain=chain,
                ),
            )
        started = time.perf_counter()
        phase = Phase(plan.split.shell, {}, {} if settings.DEBUG else None)
        work = partial(
            run_phase,
            plan.graph,
            file_path,
            phase,
            request=request,
            url_kwargs=url_kwargs,
            dep_cache=dep_cache,
        )
        with blaming() as blamed:
            outcome = (
                self._contained(chain, file_path, blamed, work)
                if chain.errors
                else work()
            )
        tail, _thunk, _entry = self._tail(
            file_path, request, url_kwargs, dep_cache, None
        )
        shell = self._shell(chain, file_path, url_kwargs, tail, blamed)
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        note_slow(plan, file_path, phase.timings, time.perf_counter() - started)
        return self._gate(
            plan, shell, outcome, (file_path, request, url_kwargs, dep_cache)
        )

    async def agated_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain,
    ) -> dict[str, object] | Caught:
        """Build what `gated_context` builds, awaiting the async shell callables."""
        plan = self.gating_plan(file_path, chain)
        started = time.perf_counter()
        phase = Phase(plan.split.shell, {}, {} if settings.DEBUG else None)
        work = arun_phase(
            plan.graph,
            file_path,
            phase,
            request=request,
            url_kwargs=url_kwargs,
            dep_cache=dep_cache,
        )
        with blaming() as blamed:
            outcome = (
                await self._acontained(chain, file_path, blamed, work)
                if chain.errors
                else await work
            )
        render = (file_path, request, url_kwargs, dep_cache)
        shell, thunk, entry = await self._ashell(
            chain, render, blamed, caught=isinstance(outcome, Caught)
        )
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        note_slow(plan, file_path, phase.timings, time.perf_counter() - started)
        context = self._gate(plan, shell, outcome, render)
        if entry is not None and entry.folded is None:
            folded = await thunk.afold(entry, dict(context))
            context[METADATA_KEY] = FoldedMetadata(thunk, folded)
        return context

    def _gate(
        self,
        plan: SplitPlan,
        shell: ErrorContext,
        results: dict[int, object],
        render: tuple[Path, HttpRequest | None, Mapping[str, object], dict[str, Any]],
    ) -> dict[str, object]:
        """Return the shell context over `results`, holding the gate of the jobs."""
        file_path, request, url_kwargs, dep_cache = render
        taken = merged(plan.graph, file_path, results)
        context = shell.around(taken)
        gate = ShellGate(
            plan,
            shell.chain,
            file_path,
            request,
            url_kwargs,
            dep_cache,
            results,
            taken,
            frozenset(shell.tail),
        )
        context[GATE_KEY] = gate
        return context


__all__ = ["PageGating"]
