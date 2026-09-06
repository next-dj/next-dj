"""Compile a per-callable injection plan from the providers' static verdicts.

A marker parameter is owned by one provider no matter the context, so the
per-request scan of every provider is pure overhead for it. The compiler asks
each provider once, keeps only the ones the signature cannot rule out, and the
resolver replays that short-list on every call.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from .providers import ParameterProvider


type ParameterPlan = tuple[
    str,
    tuple[ParameterProvider, ...],
    ParameterProvider | None,
    object,
    inspect.Parameter,
]
"""Name, candidates, terminal, fallback, and parameter, replayed on every call.

A plain tuple rather than a named one, because `UNPACK_SEQUENCE` takes its
fast path only for an exact tuple and a subclass costs more per entry.
"""

type InjectionPlan = tuple[ParameterPlan, ...]

EMPTY_PLAN: InjectionPlan = ()


def compile_plan(
    signature: inspect.Signature,
    hints: Mapping[str, Any],
    providers: Sequence[ParameterProvider],
    skips: Callable[[inspect.Parameter], bool],
) -> InjectionPlan:
    """Return one plan entry per injectable parameter of `signature`.

    Providers are walked in list order, which is already sorted by priority
    and keeps custom insertions where they were put. A verdict outside the
    three-valued contract raises from the compile rather than changing injection
    semantics silently. The walk reaches every parameter of every callable, so one
    such provider raises for all of them until it is fixed.
    """
    entries: list[ParameterPlan] = []
    empty = inspect.Parameter.empty
    for name, raw in signature.parameters.items():
        if skips(raw):
            continue
        annotation = hints.get(name, raw.annotation)
        param = raw
        if annotation is not raw.annotation:
            param = raw.replace(annotation=annotation)
        candidates: list[ParameterProvider] = []
        terminal: ParameterProvider | None = None
        for provider in providers:
            # Widened from the declared verdict, because the compiler guards
            # against a third-party hook that returns something else.
            verdict: object = provider.static_can_handle(param)
            if verdict is False:
                continue
            if verdict is None:
                candidates.append(provider)
                continue
            if verdict is not True:
                msg = (
                    f"{type(provider).__name__}.static_can_handle returned "
                    f"{verdict!r} for parameter {name!r}, expected True, False, "
                    "or None."
                )
                raise TypeError(msg)
            terminal = provider
            break
        fallback = None if param.default is empty else param.default
        entries.append((name, tuple(candidates), terminal, fallback, param))
    return tuple(entries)


__all__ = ["EMPTY_PLAN", "InjectionPlan", "ParameterPlan", "compile_plan"]
