"""Reference resolver that fills parameters without a compiled plan.

Nothing else re-derives what a compiled plan trusts blindly from providers, so this
resolver is kept as the second opinion the plan is checked against.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, override

from .introspect import (
    HINT_ERRORS,
    cached_signature,
    cached_type_hints,
    prepared_parameter,
)
from .resolver import DependencyResolver, UnknownDependencyError


if TYPE_CHECKING:
    from collections.abc import Callable

    from .context import ResolutionContext


type _FillTarget = tuple[str, inspect.Parameter, object]
"""Name, resolved parameter, and fallback of one parameter the walk fills."""


class LinearDependencyResolver(DependencyResolver):
    """Fill each parameter from the first provider whose `can_handle` claims it.

    Selectable via `NEXT_FRAMEWORK["DEPENDENCY_RESOLVER"]`, it picks providers
    independently of `compile_plan`, at the cost of a full walk per parameter.
    """

    def _fill_targets(self, func: Callable[..., Any]) -> tuple[_FillTarget, ...]:
        """Return the parameters of `func` to fill, prepared the way a compile does.

        Unresolved hints leave the raw annotations in place, so a name only a later
        import defines is picked up on the next resolve rather than frozen out.
        """
        try:
            signature = cached_signature(func)
        except (ValueError, TypeError):
            return ()
        try:
            hints: dict[str, Any] = cached_type_hints(func)
        except HINT_ERRORS:
            hints = {}
        targets: list[_FillTarget] = []
        for name, raw in signature.parameters.items():
            if self.skips(raw):
                continue
            param, fallback = prepared_parameter(name, raw, hints)
            targets.append((name, param, fallback))
        return tuple(targets)

    @override
    def resolve[T](
        self, func: Callable[..., T], context: ResolutionContext
    ) -> dict[str, Any]:
        """Return keyword arguments for `func` by asking every provider in turn.

        A missing dependency names the callable filled here, matching the plan replay.
        """
        self._sync_providers()
        providers = self._providers
        result: dict[str, Any] = {}
        try:
            for name, param, fallback in self._fill_targets(func):
                for provider in providers:
                    if provider.can_handle(param, context):
                        result[name] = provider.resolve(param, context)
                        break
                else:
                    result[name] = fallback
        except UnknownDependencyError as exc:
            exc.attribute_to(func)
            raise
        return result

    @override
    def provides(
        self,
        func: Callable[..., Any],
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Return whether a provider fills `param` of `func` in `context`.

        The walk sees the signature's own parameter, so a foreign name is never claimed.
        """
        self._sync_providers()
        target = next(
            (
                resolved
                for name, resolved, _fallback in self._fill_targets(func)
                if name == param.name
            ),
            None,
        )
        if target is None:
            return False
        return any(provider.can_handle(target, context) for provider in self._providers)


__all__ = ["LinearDependencyResolver"]
