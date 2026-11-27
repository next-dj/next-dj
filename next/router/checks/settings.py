"""System checks for the keys, the backend seams and the speculation of `ROUTER`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.checks import CheckMessage, Error

from next.checks import NEXT, register
from next.checks.common import class_problem, raw_scope
from next.conf.checks import error_for_misfit, error_for_unusable_path
from next.conf.router import SCOPE, SHAPE, Eagerness, speculation_problem
from next.conf.shapes import alternatives, misfits
from next.router.backends import ChromeDigest, NavigationCut, PrefetchShed


if TYPE_CHECKING:
    from collections.abc import Iterator


PREFIX: Final = f"NEXT_FRAMEWORK[{SCOPE!r}]"


@register(NEXT)
def check_router_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys and value shapes of `ROUTER` (`next.E035`, `next.E076`)."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    return [error_for_misfit(PREFIX, misfit) for misfit in misfits(scope, SHAPE)]


_NAVIGATION_FAILS: Final = "every navigation raises ImproperlyConfigured"
_PREFETCH_FAILS: Final = "every prefetch raises ImproperlyConfigured"


def _seams(scope: Mapping[str, object]) -> Iterator[tuple[str, object, type, str]]:
    """Yield the address, the written value, the base and the cost of each seam."""
    yield f"{PREFIX}['CUT']", scope.get("CUT"), NavigationCut, _NAVIGATION_FAILS
    yield f"{PREFIX}['DIGEST']", scope.get("DIGEST"), ChromeDigest, _NAVIGATION_FAILS
    yield f"{PREFIX}['SHED']", scope.get("SHED"), PrefetchShed, _PREFETCH_FAILS


@register(NEXT)
def check_router_seams(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a router backend path that names no usable class (`next.E219`)."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    errors: list[CheckMessage] = []
    for where, dotted, base, cost in _seams(scope):
        if not isinstance(dotted, str) or not dotted:
            continue
        problem = class_problem(dotted, base)
        if problem is not None:
            errors.append(
                error_for_unusable_path(
                    where,
                    dotted,
                    problem,
                    cost,
                    f"Name a concrete subclass of {base.__name__}.",
                )
            )
    return errors


_SPECULATION: Final = "SPECULATION"


@register(NEXT)
def check_speculation(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `SPECULATION` value that emits no rules (`next.E242`)."""
    scope = raw_scope(SCOPE)
    value = None if scope is None else scope.get(_SPECULATION)
    if not isinstance(value, str | Mapping):
        return []
    problem = speculation_problem(value)
    if problem is None:
        return []
    return [
        Error(
            f"{PREFIX}[{_SPECULATION!r}] {problem}, so documents carry no "
            "Speculation Rules.",
            hint=f"Name the eagerness {alternatives(Eagerness)}, as a string or "
            "under 'eagerness'. Only prefetch rules are emitted.",
            obj=settings,
            id="next.E242",
        )
    ]


__all__ = ["PREFIX", "check_router_seams", "check_router_settings", "check_speculation"]
