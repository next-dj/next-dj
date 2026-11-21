"""The dependency graph the context callables of an async page render run along."""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast

from next.deps.cache import DependencyCache, request_memo
from next.deps.context import RESERVED_KEYS, ResolutionContext
from next.deps.introspect import (
    HINT_ERRORS,
    cached_signature,
    cached_type_hints,
    prepared_parameter,
)
from next.deps.markers import Depends, DependsProvider, factory_key
from next.deps.objects import SCOPE_DEPENDENCY, ObjectProvider, object_target
from next.deps.resolver import current_resolver
from next.deps.runner import run_isolated
from next.introspect import callable_name

from .context import (
    Context,
    ContextByDefaultProvider,
    ContextByNameProvider,
    ContextResult,
)
from .errors import PageContextShapeError


if TYPE_CHECKING:
    import inspect
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.deps.providers import ParameterProvider
    from next.deps.resolver import DependencyResolver
    from next.static.serializers import JsContextSerializer

    from .registry import PageContextEntry


_ABSENT: Final = object()
_PENDING: Final = object()
_SKIPPED: Final = object()

_LOCAL_STORAGE: Final = "asgiref.local"

_PASSIVE: Final = frozenset(
    {
        "next.consent.providers.ConsentProvider",
        "next.deps.objects.UnservedObjectProvider",
        "next.forms.markers.CleanedDataProvider",
        "next.forms.markers.FormProvider",
        "next.urls.markers.HttpRequestProvider",
        "next.urls.markers.QueryParamProvider",
        "next.urls.markers.UrlByAnnotationProvider",
        "next.urls.markers.UrlKwargsProvider",
    }
)
"""The built-in providers that fill a parameter without reading the context data."""


class _Patch(NamedTuple):
    """The attributes one callable set and deleted in an `asgiref.local` storage."""

    changed: dict[str, object]
    dropped: frozenset[str]


type _Writes = dict[contextvars.ContextVar[Any], object]


class Blame:
    """The inherited group a context run failed in, `-1` for the page's own ones.

    It also keeps each inherited result in run order, so a boundary can cut the shell.
    """

    __slots__ = ("group", "settled")

    def __init__(self) -> None:
        """Start on the page's own callables, which a failure elsewhere moves off."""
        self.group = -1
        self.settled: list[tuple[int, str | None, object]] = []

    def settle(self, group: int, key: str | None, value: object) -> None:
        """Keep what one callable of the inherited `group` returned."""
        self.settled.append((group, key, value))

    def level(self, inherited: tuple[int, ...]) -> int:
        """Return the directory of the failed group, `-1` for the page's own ones."""
        group = self.group
        return inherited[group] if 0 <= group < len(inherited) else -1

    def cut(self, groups: int) -> dict[str, object]:
        """Return the context the first `groups` inherited groups merged."""
        merged: dict[str, object] = {}
        for group, key, value in self.settled:
            if group >= groups:
                continue
            if key is not None:
                merged[key] = value
                continue
            for name, item in cast("dict[str, object]", value).items():
                merged.setdefault(name, item)
        return merged


_blame: contextvars.ContextVar[Blame | None] = contextvars.ContextVar(
    "next_context_blame", default=None
)


def watching() -> Blame | None:
    """Return the blame the current block keeps, `None` outside `blaming`."""
    return _blame.get()


def blame(group: int) -> None:
    """Record that a callable of `group` failed, for the block `blaming` watches."""
    held = _blame.get()
    if held is not None:
        held.group = group


@contextmanager
def blaming() -> Iterator[Blame]:
    """Watch the context runs of the block for the group a failure belongs to."""
    held = Blame()
    token = _blame.set(held)
    try:
        yield held
    finally:
        _blame.reset(token)


def keyless_shape_error(
    func: Callable[..., object], file_path: Path
) -> PageContextShapeError:
    """Return the error a keyless `@context` returning a non-mapping raises."""
    return PageContextShapeError(callable_name(func), file_path)


def keyless_result(
    result: object,
    entry: PageContextEntry | Step,
    file_path: Path,
    shielded: frozenset[str],
) -> dict[str, object]:
    """Return the mapping a keyless callable returned, without the shielded keys."""
    if not isinstance(result, dict):
        raise keyless_shape_error(entry.func, file_path)
    if not shielded:
        return result
    return {name: value for name, value in result.items() if name not in shielded}


class Step(NamedTuple):
    """One context callable of a render in run order, `group` -1 for the page's own.

    An ancestor callable carries its file's group, and `inherits` marks an own one.
    """

    key: str | None
    func: Callable[..., object]
    is_async: bool
    serialize: bool
    serializer: JsContextSerializer | None
    zones: frozenset[str] | None
    group: int
    guard: bool | None = None
    inherits: bool = False


class _Reads(NamedTuple):
    """The context keys a callable may read, `direct` the ones its parameters name.

    `names` holds the dependency and factory keys the callable reaches, each one once.
    """

    keys: frozenset[str]
    every: bool
    direct: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()


_NOTHING: Final = _Reads(frozenset(), every=False)
_EVERY: Final = _Reads(frozenset(), every=True)


def _joined(first: _Reads, second: _Reads) -> _Reads:
    """Return what `first` and `second` read together."""
    return _Reads(
        first.keys | second.keys,
        first.every or second.every,
        first.direct | second.direct,
        first.names | second.names,
    )


def _nested(reads: _Reads, names: frozenset[str] = frozenset()) -> _Reads:
    """Return `reads` as a dependency makes them, none of them direct."""
    return _Reads(reads.keys, reads.every, frozenset(), reads.names | names)


def _address(provider: ParameterProvider) -> str:
    """Return the dotted address of the class of `provider`."""
    kind = type(provider)
    return f"{kind.__module__}.{kind.__qualname__}"


@dataclass(frozen=True, slots=True)
class ContextGraph:
    """The run order of a page split into layers of callables that read no sibling.

    An attribute an earlier callable sets on `request` is a read no edge can see.
    """

    steps: tuple[Step, ...]
    needs: tuple[frozenset[int], ...]
    layers: tuple[tuple[int, ...], ...]
    parallel: bool
    shielded: frozenset[str]
    reads: tuple[frozenset[int], ...] = ()
    shared: frozenset[str] = frozenset()
    providers: tuple[ParameterProvider, ...] = ()
    bindings: tuple[tuple[str, Callable[..., object] | None], ...] = ()

    def current(self, resolver: DependencyResolver) -> bool:
        """Return whether `resolver` holds the providers and bindings the graph read."""
        return resolver.providers == self.providers and all(
            resolver.get_dependency(name) is bound for name, bound in self.bindings
        )


class _Analysis:
    """Find what each parameter reads through the provider the resolver claims it by.

    A provider outside the known set may read any key, so its parameter reads all.
    """

    __slots__ = ("bindings", "providers", "resolver")

    def __init__(self, resolver: DependencyResolver) -> None:
        """Read the providers once, so the whole graph sees one list."""
        self.resolver = resolver
        self.providers = resolver.providers
        self.bindings: dict[str, Callable[..., object] | None] = {}

    def callable_reads(
        self, func: Callable[..., object], seen: frozenset[str | int]
    ) -> _Reads:
        """Return what the parameters of `func` read, a callable seen before nothing."""
        if id(func) in seen:
            return _NOTHING
        try:
            signature = cached_signature(func)
        except (ValueError, TypeError):
            return _NOTHING
        try:
            hints = cached_type_hints(func)
        except HINT_ERRORS:
            hints = {}
        inner = seen | {id(func)}
        found = _NOTHING
        skips = self.resolver.skips
        for name, raw in signature.parameters.items():
            if skips(raw):
                continue
            param, _fallback = prepared_parameter(name, raw, hints)
            found = _joined(found, self._param_reads(param, inner))
        return found

    def dependency_reads(self, name: str, seen: frozenset[str | int]) -> _Reads:
        """Return what the dependency bound to `name` reads, a cycle reading nothing."""
        if name in seen:
            return _NOTHING
        bound = self.resolver.get_dependency(name)
        self.bindings[name] = bound
        if bound is None:
            return _NOTHING
        found = self.callable_reads(bound, seen | {name})
        return _nested(found, frozenset({name}))

    def _param_reads(
        self, param: inspect.Parameter, seen: frozenset[str | int]
    ) -> _Reads:
        """Return what the providers that may fill `param` read, in claim order."""
        found = _NOTHING
        for provider in self.providers:
            verdict = provider.static_can_handle(param)
            if verdict is False:
                continue
            found = _joined(found, self._provider_reads(provider, param, seen))
            if verdict is True:
                break
        return found

    def _provider_reads(
        self,
        provider: ParameterProvider,
        param: inspect.Parameter,
        seen: frozenset[str | int],
    ) -> _Reads:
        """Return what `provider` reads to fill `param`."""
        kind = type(provider)
        if kind is ContextByNameProvider:
            key = frozenset({param.name})
            return _Reads(key, every=False, direct=key)
        if kind is ContextByDefaultProvider:
            return self._marker_reads(param, seen)
        if kind is DependsProvider:
            return self._depends_reads(param, seen)
        if kind is ObjectProvider:
            return self._object_reads(param, seen)
        if _address(provider) in _PASSIVE:
            return _NOTHING
        return _EVERY

    def _marker_reads(
        self, param: inspect.Parameter, seen: frozenset[str | int]
    ) -> _Reads:
        """Return what a `Context` default reads, through its source when callable."""
        marker: Context = param.default
        source = marker.source
        if source is None or isinstance(source, str):
            key = param.name if source is None else source
            if key in RESERVED_KEYS:
                return _NOTHING
            keys = frozenset({key})
            return _Reads(keys, every=False, direct=keys)
        if callable(source):
            return _nested(self.callable_reads(source, seen))
        return _NOTHING

    def _depends_reads(
        self, param: inspect.Parameter, seen: frozenset[str | int]
    ) -> _Reads:
        """Return what the target of a `Depends` default reads, by name or factory."""
        marker: Depends = param.default
        target = param.name if marker.dependency is None else marker.dependency
        if isinstance(target, str):
            return self.dependency_reads(target, seen)
        if callable(target):
            reads = self.callable_reads(target, seen)
            return _nested(reads, frozenset({factory_key(target)}))
        return _NOTHING

    def _object_reads(
        self, param: inspect.Parameter, seen: frozenset[str | int]
    ) -> _Reads:
        """Return the parent key a `DObject` reads and what its `scope` reads."""
        target = object_target(param.annotation)
        parent = None if target is None else target[1]
        scope = self.dependency_reads(SCOPE_DEPENDENCY, seen)
        keys = scope.keys if parent is None else scope.keys | {parent}
        return _Reads(keys, scope.every, frozenset(), scope.names)


def callable_keys(
    func: Callable[..., object], resolver: DependencyResolver
) -> frozenset[str]:
    """Return the context keys the parameters of `func` name, through any dependency."""
    return _Analysis(resolver).callable_reads(func, frozenset()).keys


def closure(graph: ContextGraph, start: frozenset[int]) -> frozenset[int]:
    """Return `start` with every step its members read, directly or through others."""
    found = set(start)
    pending = list(start)
    while pending:
        for earlier in graph.needs[pending.pop()]:
            if earlier not in found:
                found.add(earlier)
                pending.append(earlier)
    return frozenset(found)


def _needs(steps: tuple[Step, ...], index: int, reads: _Reads) -> frozenset[int]:
    """Return the earlier steps whose result the step at `index` reads.

    An inherited keyed step reads the keys before it to decide whether it runs at all.
    """
    step = steps[index]
    decides = step.group >= 0 and step.key is not None
    by_name = bool(reads.keys)
    found: set[int] = set()
    for earlier, before in enumerate(steps[:index]):
        if reads.every:
            found.add(earlier)
        elif before.key is None:
            if by_name or (decides and before.group >= 0):
                found.add(earlier)
        elif before.key in reads.keys or (
            decides and before.group >= 0 and before.key == step.key
        ):
            found.add(earlier)
    return frozenset(found)


def _named(
    steps: tuple[Step, ...], index: int, direct: frozenset[str]
) -> frozenset[int]:
    """Return the earlier keyed steps whose key a parameter of `index` names."""
    return frozenset(
        earlier
        for earlier, before in enumerate(steps[:index])
        if before.key is not None and before.key in direct
    )


def compile_graph(
    steps: tuple[Step, ...], shielded: frozenset[str], resolver: DependencyResolver
) -> ContextGraph:
    """Compile the layers of `steps`, each step one layer below the last it reads."""
    analysis = _Analysis(resolver)
    needs: list[frozenset[int]] = []
    named: list[frozenset[int]] = []
    levels: list[int] = []
    counts: Counter[str] = Counter()
    for index, step in enumerate(steps):
        reads = analysis.callable_reads(step.func, frozenset())
        found = _needs(steps, index, reads)
        needs.append(found)
        named.append(_named(steps, index, reads.direct))
        counts.update(reads.names)
        levels.append(1 + max((levels[earlier] for earlier in found), default=-1))
    layers = tuple(
        tuple(index for index, found in enumerate(levels) if found == level)
        for level in range(max(levels, default=-1) + 1)
    )
    parallel = any(
        sum(steps[index].is_async for index in layer) > 1 for layer in layers
    )
    return ContextGraph(
        steps,
        tuple(needs),
        layers,
        parallel,
        shielded,
        reads=tuple(named),
        shared=frozenset(name for name, count in counts.items() if count > 1),
        providers=analysis.providers,
        bindings=tuple(analysis.bindings.items()),
    )


class _Merge:
    """Merge results in run order exactly as the sync collector does.

    It also answers whether an inherited keyed step runs, from the keys merged so far.
    """

    __slots__ = (
        "_file_path",
        "_graph",
        "_group",
        "_merged",
        "_page",
        "inherited",
        "js_context",
        "serializers",
    )

    def __init__(self, graph: ContextGraph, file_path: Path) -> None:
        """Start with nothing merged."""
        self._graph = graph
        self._file_path = file_path
        self.inherited: dict[str, object] = {}
        self._page: dict[str, object] | None = None
        self.js_context: dict[str, object] = {}
        self.serializers: dict[str, JsContextSerializer] = {}
        self._group = -1
        self._merged: tuple[str, ...] = ()

    def _enter(self, group: int) -> None:
        if group != self._group:
            self._group = group
            self._merged = ()

    def page(self) -> dict[str, object]:
        """Return the page context, which starts as a copy of the inherited one."""
        if self._page is None:
            self._page = dict(self.inherited)
        return self._page

    def view(self, index: int) -> dict[str, object]:
        """Return the mapping the step at `index` reads by name."""
        if self._graph.steps[index].group >= 0:
            return self.inherited
        return self.page()

    def runs(self, index: int) -> bool:
        """Return whether the step at `index` runs, False for an inherited key taken."""
        step = self._graph.steps[index]
        if step.group < 0 or step.key is None:
            return True
        self._enter(step.group)
        return step.key not in self.inherited or step.key in self._merged

    def add(self, index: int, value: object) -> None:
        """Merge the result of the step at `index`."""
        step = self._graph.steps[index]
        if step.group >= 0:
            self._inherit(step, value)
        else:
            self._own(step, value)

    def _inherit(self, step: Step, value: object) -> None:
        """Merge an ancestor result, a keyless one adding only the names still free."""
        self._enter(step.group)
        if step.key is not None:
            self.inherited[step.key] = value
            return
        if not isinstance(value, dict):
            raise keyless_shape_error(step.func, self._file_path)
        merged = tuple(name for name in value if name not in self.inherited)
        for name in merged:
            self.inherited[name] = value[name]
        self._merged = merged

    def _own(self, step: Step, value: object) -> None:
        """Merge a result of the page itself and publish what it serializes."""
        context_data = self.page()
        key = step.key
        if key is None:
            result = keyless_result(value, step, self._file_path, self._graph.shielded)
            context_data.update(result)
            if step.serialize:
                self.js_context.update(result)
                if step.serializer is not None:
                    for name in result:
                        self.serializers[name] = step.serializer
            return
        context_data[key] = value
        if step.serialize and key not in self.js_context:
            self.js_context[key] = value
            if step.serializer is not None:
                self.serializers[key] = step.serializer

    def result(self) -> ContextResult:
        """Return the merged context."""
        return ContextResult(
            context_data=self.page(),
            js_context=self.js_context,
            js_context_serializers=self.serializers,
        )


def _patch(value: dict[str, object], before: object) -> _Patch:
    """Return the attributes `value` sets or drops against the storage `before`."""
    held: dict[str, object] = before if isinstance(before, dict) else {}
    return _Patch(
        {
            name: item
            for name, item in value.items()
            if held.get(name, _ABSENT) is not item
        },
        frozenset(name for name in held if name not in value),
    )


def _writes(scope: contextvars.Context, start: contextvars.Context) -> _Writes:
    """Return the variables `scope` holds another value of than `start` does.

    An `asgiref.local` storage is diffed by attribute, as one write copies all of them.
    It is told by the `asgiref.local` name of its variable and the dict it holds.
    """
    writes: _Writes = {}
    for variable, value in scope.items():
        before = start.get(variable, _ABSENT)
        if before is value:
            continue
        if variable.name == _LOCAL_STORAGE and isinstance(value, dict):
            patch = _patch(value, before)
            if patch.changed or patch.dropped:
                writes[variable] = patch
            continue
        writes[variable] = value
    return writes


def _restore(writes: _Writes) -> None:
    """Set each written variable in the current context, as asgiref restores one.

    A storage patch applies its attributes over the storage the caller holds now.
    """
    for variable, value in writes.items():
        if isinstance(value, _Patch):
            merged = {**variable.get({}), **value.changed}
            for name in value.dropped:
                merged.pop(name, None)
            variable.set(merged)
            continue
        current = variable.get(_ABSENT)
        if current is _ABSENT or current != value:
            variable.set(value)


def _retrieved(task: asyncio.Task[object]) -> None:
    """Read the error of a finished task, so one the run abandons is never logged."""
    if not task.cancelled():
        task.exception()


async def _stop(tasks: list[asyncio.Task[object]]) -> None:
    """Cancel the tasks still running and wait until each of them has ended."""
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)


def _failure(task: asyncio.Task[object]) -> BaseException | None:
    """Return the error `task` ended with, None for a result or a cancel."""
    return None if task.cancelled() else task.exception()


class _Run:
    """One async collection of a page context along its compiled graph."""

    __slots__ = (
        "base",
        "error",
        "file_path",
        "graph",
        "groups",
        "limit",
        "prior",
        "request",
        "requested_zones",
        "resolver",
        "selected",
        "taken",
        "timings",
        "url",
        "url_kwargs",
        "watch",
    )

    def __init__(
        self,
        graph: ContextGraph,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, object],
        requested_zones: frozenset[str] | None,
        groups: int | None = None,
    ) -> None:
        """Hold what every step resolves against, a `groups` count cutting the run."""
        self.graph = graph
        self.groups = groups
        self.selected: frozenset[int] | None = None
        self.prior: Mapping[int, object] = {}
        self.timings: dict[int, float] | None = None
        self.taken: dict[int, object] = {}
        self.watch = _blame.get()
        self.file_path = file_path
        self.request = request
        self.url_kwargs = url_kwargs
        self.requested_zones = requested_zones
        self.resolver: DependencyResolver = current_resolver()
        self.url = {
            name: value
            for name, value in url_kwargs.items()
            if name not in RESERVED_KEYS
        }
        self.base = ResolutionContext(
            request=request,
            form=None,
            url_kwargs=self.url,
            context_data={},
            cache=DependencyCache(dep_cache),
        )
        self.limit = len(graph.steps)
        self.error: BaseException | None = None

    def select(self, phase: Phase) -> None:
        """Run only the steps of `phase`, over the results earlier phases took."""
        self.selected = phase.selected
        self.prior = phase.prior
        self.timings = phase.timings

    def skipped(self, index: int) -> bool:
        """Return whether a zone GET or a cut to the leading groups skips `index`."""
        step = self.graph.steps[index]
        groups = self.groups
        if groups is not None and not 0 <= step.group < groups:
            return True
        selected = self.selected
        if selected is not None and index not in selected:
            return True
        zones = step.zones
        requested = self.requested_zones
        return (
            requested is not None and zones is not None and zones.isdisjoint(requested)
        )

    def context(self, view: Mapping[str, object]) -> ResolutionContext:
        """Return a resolution context reading `view`, with a stack of its own."""
        return replace(self.base, context_data=view, stack=[])

    def call(self, index: int, view: Mapping[str, object], cache: object) -> object:
        """Call a sync step the way the sync collector does, on a blocking thread."""
        func = self.graph.steps[index].func
        started = time.perf_counter()
        resolved = self.resolver.resolve_dependencies(
            func,
            request=self.request,
            _cache=cache,
            _stack=[],
            _context_data=view,
            **self.url_kwargs,
        )
        value = func(**resolved)
        self.timed(index, started)
        return value

    async def acall(self, index: int, view: Mapping[str, object]) -> object:
        """Await one async step against `view`."""
        started = time.perf_counter()
        value = await self.resolver.acall(
            self.graph.steps[index].func, self.context(view)
        )
        self.timed(index, started)
        return value

    def timed(self, index: int, started: float) -> None:
        """Keep how long the step at `index` took, when the run keeps timings."""
        if self.timings is not None:
            self.timings[index] = time.perf_counter() - started

    async def serial(self) -> ContextResult:
        """Run every step in order, a run of sync steps as one blocking segment."""
        merge = _Merge(self.graph, self.file_path)
        batch: list[int] = []
        prior = self.prior
        for index, step in enumerate(self.graph.steps):
            if index in prior:
                if batch:
                    await run_isolated(
                        partial(self._batch, tuple(batch), merge), self.base
                    )
                    batch = []
                merge.add(index, prior[index])
                continue
            if self.skipped(index):
                continue
            if not step.is_async:
                batch.append(index)
                continue
            if batch:
                await run_isolated(partial(self._batch, tuple(batch), merge), self.base)
                batch = []
            if merge.runs(index):
                try:
                    value = await self.acall(index, merge.view(index))
                    merge.add(index, value)
                except Exception:
                    blame(step.group)
                    raise
                self.settle(index, value)
        if batch:
            await run_isolated(partial(self._batch, tuple(batch), merge), self.base)
        return merge.result()

    def _batch(
        self, indices: tuple[int, ...], merge: _Merge, context: ResolutionContext
    ) -> None:
        """Run consecutive sync steps against the live merge, in order."""
        steps = self.graph.steps
        for index in indices:
            if merge.runs(index):
                try:
                    value = self.call(index, merge.view(index), context.cache)
                    merge.add(index, value)
                except Exception:
                    blame(steps[index].group)
                    raise
                self.settle(index, value)

    def settle(self, index: int, value: object) -> None:
        """Keep the result of a step, an inherited one for the blame that watches."""
        self.taken[index] = value
        watch = self.watch
        step = self.graph.steps[index]
        if watch is not None and step.group >= 0:
            watch.settle(step.group, step.key, value)

    async def parallel(self) -> ContextResult:
        """Run the layers in order, overlapping the async steps of each layer.

        The failure a sequential run meets first is raised once no step before it runs.
        A dependency two steps name is shared, so the overlap computes it once.
        """
        memo = request_memo.get()
        shared = self.graph.shared
        token = (
            None
            if memo is None or shared <= memo.shared
            else request_memo.set(replace(memo, shared=memo.shared | shared))
        )
        results: list[object] = [_PENDING] * len(self.graph.steps)
        prior = self.prior
        for index, value in prior.items():
            results[index] = value
        try:
            for layer in self.graph.layers:
                await self._layer(layer, results)
        finally:
            if token is not None:
                request_memo.reset(token)
        for index, value in enumerate(results[: self.limit]):
            if value is not _PENDING and value is not _SKIPPED and index not in prior:
                self.settle(index, value)
        if self.error is not None:
            raise self.error
        merge = _Merge(self.graph, self.file_path)
        for index, value in enumerate(results):
            if value is not _SKIPPED:
                merge.add(index, value)
        return merge.result()

    def _fail(self, index: int, error: BaseException) -> None:
        """Keep the failure earliest in run order, the one a sequential run raises."""
        if index < self.limit:
            self.limit = index
            self.error = error
            blame(self.graph.steps[index].group)

    def _fold(self, results: list[object], index: int) -> _Merge:
        """Merge the finished results before `index`, what the step there may read."""
        fold = _Merge(self.graph, self.file_path)
        for earlier, value in enumerate(results[:index]):
            if value is not _PENDING and value is not _SKIPPED:
                fold.add(earlier, value)
        return fold

    async def _layer(self, layer: tuple[int, ...], results: list[object]) -> None:
        """Run the steps of one layer that come before the earliest failure."""
        calls: list[tuple[int, dict[str, object]]] = []
        for index in layer:
            if index >= self.limit:
                break
            if results[index] is not _PENDING:
                continue
            fold = None if self.skipped(index) else self._fold(results, index)
            if fold is None or not fold.runs(index):
                results[index] = _SKIPPED
                continue
            calls.append((index, fold.view(index)))
        if calls:
            await self._overlap(calls, results)

    def _shaped(self, index: int, value: object) -> object:
        """Return `value`, raising the shape error for a keyless step's non-mapping."""
        step = self.graph.steps[index]
        if step.key is None and not isinstance(value, dict):
            raise keyless_shape_error(step.func, self.file_path)
        return value

    async def _step(self, index: int, view: dict[str, object]) -> object:
        """Await one async step, failing at once on a keyless step's wrong shape."""
        return self._shaped(index, await self.acall(index, view))

    def _lane(
        self,
        lane: tuple[tuple[int, dict[str, object]], ...],
        done: list[tuple[object, _Writes]],
        stop: list[int],
        context: ResolutionContext,
    ) -> None:
        """Run the sync steps of a layer in order, each in a context copy of its own.

        It stops before a step past `stop`, which a failing sibling lowers.
        """
        start = contextvars.copy_context()
        for index, view in lane:
            if index > stop[0]:
                return
            scope = start.copy()
            value = scope.run(self.call, index, view, context.cache)
            done.append((self._shaped(index, value), _writes(scope, start)))

    async def _overlap(
        self, calls: list[tuple[int, dict[str, object]]], results: list[object]
    ) -> None:
        """Run the async steps as tasks beside one segment of the sync steps.

        A failure cancels the siblings after it and lets the ones before it finish.
        """
        start = contextvars.copy_context()
        steps = self.graph.steps
        owned: list[tuple[int, asyncio.Task[object], contextvars.Context]] = []
        for index, view in calls:
            if steps[index].is_async:
                scope = start.copy()
                task = asyncio.create_task(self._step(index, view), context=scope)
                task.add_done_callback(_retrieved)
                owned.append((index, task, scope))
        lane = tuple(call for call in calls if not steps[call[0]].is_async)
        lane_run = _Lane(lane, self.limit)
        tasks = [task for _index, task, _scope in owned]
        if lane:
            lane_run.task = asyncio.create_task(
                run_isolated(
                    partial(self._lane, lane, lane_run.done, lane_run.stop), self.base
                )
            )
            lane_run.task.add_done_callback(_retrieved)
            tasks.append(lane_run.task)
        try:
            await self._watch(owned, lane_run, tasks)
        finally:
            await _stop(tasks)
        writes: dict[int, _Writes] = {}
        for index, task, scope in owned:
            if _failure(task) is None and not task.cancelled():
                results[index] = task.result()
                writes[index] = _writes(scope, start)
        done = lane_run.done
        for (index, _view), (value, written) in zip(
            lane[: len(done)], done, strict=True
        ):
            results[index] = value
            writes[index] = written
        for index, _view in calls:
            if index < self.limit and index in writes:
                _restore(writes[index])

    async def _watch(
        self,
        owned: list[tuple[int, asyncio.Task[object], contextvars.Context]],
        lane: _Lane,
        tasks: list[asyncio.Task[object]],
    ) -> None:
        """Wait for the tasks, cancelling the async steps after each new failure."""
        pending = set(tasks)
        while pending:
            _finished, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_EXCEPTION
            )
            for index, task, _scope in owned:
                if task.done() and (error := _failure(task)) is not None:
                    self._fail(index, error)
            lane_task = lane.task
            if lane_task is not None and lane_task.done():
                error = _failure(lane_task)
                if error is not None:
                    self._fail(lane.steps[len(lane.done)][0], error)
            lane.stop[0] = self.limit
            for index, task, _scope in owned:
                if index > self.limit:
                    task.cancel()


class _Lane:
    """The sync steps of one layer, the task running them and what they finished."""

    __slots__ = ("done", "steps", "stop", "task")

    def __init__(
        self, steps: tuple[tuple[int, dict[str, object]], ...], stop: int
    ) -> None:
        """Start with no step finished, stopping before any step past `stop`."""
        self.steps = steps
        self.done: list[tuple[object, _Writes]] = []
        self.stop = [stop]
        self.task: asyncio.Task[object] | None = None


@dataclass(frozen=True, slots=True)
class Phase:
    """The steps one part of a gated render runs, over what earlier parts took.

    `timings` collects how long each step took, when the caller keeps them.
    """

    selected: frozenset[int]
    prior: Mapping[int, object]
    timings: dict[int, float] | None = None


def run_phase(
    graph: ContextGraph,
    file_path: Path,
    phase: Phase,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
) -> dict[int, object]:
    """Run the selected steps of `graph` in order and return what each one returned.

    Results merge exactly as the sync collector merges them, earlier phases first.
    """
    merge = _Merge(graph, file_path)
    taken: dict[int, object] = {}
    active = current_resolver()
    watch = _blame.get()
    timings = phase.timings
    for index, step in enumerate(graph.steps):
        if index in phase.prior:
            merge.add(index, phase.prior[index])
            continue
        if index not in phase.selected or not merge.runs(index):
            continue
        started = time.perf_counter()
        try:
            resolved = active.resolve_dependencies(
                step.func,
                request=request,
                _cache=dep_cache,
                _stack=[],
                _context_data=merge.view(index),
                **url_kwargs,
            )
            value = step.func(**resolved)
            merge.add(index, value)
        except Exception:
            blame(step.group)
            raise
        if timings is not None:
            timings[index] = time.perf_counter() - started
        taken[index] = value
        if watch is not None and step.group >= 0:
            watch.settle(step.group, step.key, value)
    return taken


async def arun_phase(
    graph: ContextGraph,
    file_path: Path,
    phase: Phase,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
) -> dict[int, object]:
    """Run what `run_phase` runs along the graph, overlapping the async steps."""
    run = _Run(graph, file_path, request, url_kwargs, dep_cache, None)
    run.select(phase)
    if graph.parallel:
        await run.parallel()
    else:
        await run.serial()
    return run.taken


def merged(
    graph: ContextGraph, file_path: Path, results: Mapping[int, object]
) -> ContextResult:
    """Return the context `results` merge into, in run order."""
    merge = _Merge(graph, file_path)
    for index in sorted(results):
        merge.add(index, results[index])
    return merge.result()


async def acollect(
    graph: ContextGraph,
    file_path: Path,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
    requested_zones: frozenset[str] | None,
    groups: int | None = None,
) -> ContextResult:
    """Collect the context of `graph`, serially unless a layer overlaps async steps.

    A `groups` count runs only that many leading inherited groups, for a shell.
    """
    run = _Run(
        graph, file_path, request, url_kwargs, dep_cache, requested_zones, groups
    )
    if graph.parallel:
        return await run.parallel()
    return await run.serial()


__all__ = [
    "Blame",
    "ContextGraph",
    "Phase",
    "Step",
    "acollect",
    "arun_phase",
    "blame",
    "blaming",
    "callable_keys",
    "closure",
    "compile_graph",
    "keyless_result",
    "keyless_shape_error",
    "merged",
    "run_phase",
    "watching",
]
