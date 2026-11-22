"""The compiled graph of a page context, each callable layered under what it reads."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, NamedTuple

from next.deps.context import RESERVED_KEYS
from next.deps.introspect import (
    HINT_ERRORS,
    cached_signature,
    cached_type_hints,
    prepared_parameter,
)
from next.deps.markers import Depends, DependsProvider, factory_key
from next.deps.objects import SCOPE_DEPENDENCY, ObjectProvider, object_target
from next.pages.context import Context, ContextByDefaultProvider, ContextByNameProvider


if TYPE_CHECKING:
    import inspect
    from collections.abc import Callable

    from next.deps.providers import ParameterProvider
    from next.deps.resolver import DependencyResolver

    from .merge import Step


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
    by_level: dict[int, list[int]] = {}
    for index, level in enumerate(levels):
        by_level.setdefault(level, []).append(index)
    layers = tuple(tuple(by_level[level]) for level in range(len(by_level)))
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


__all__ = ["ContextGraph", "callable_keys", "closure", "compile_graph"]
