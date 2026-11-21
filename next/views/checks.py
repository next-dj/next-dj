"""System checks for the transactions next.dj views open, settings and declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.db import connections
from django.utils.module_loading import import_string

from next.checks import NEXT
from next.checks.common import error_for_wrong_shape, errors_for_unknown_keys, raw_scope
from next.conf.checks import error_for_unusable_seam
from next.conf.defaults import DEFAULTS
from next.forms.checks.sources import diagnostics
from next.pages.transactions import atomic_declarations
from next.transactions import alias_list, aliases_usable, flag_usable, rule_usable


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


_SCOPE: Final = "TRANSACTIONS"
_PREFIX: Final = f"NEXT_FRAMEWORK[{_SCOPE!r}]"
_KEYS: Final = frozenset(DEFAULTS[_SCOPE])

_SHAPES: Final[dict[str, tuple[Callable[[object], bool], str]]] = {
    "SAFE": (flag_usable, "a bool"),
    "UNSAFE": (flag_usable, "a bool"),
    "ALIASES": (aliases_usable, "'all' or a list of database aliases"),
    "RULE": (rule_usable, "the dotted path of a callable or None"),
}


def _shape_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    """Report every `TRANSACTIONS` value the transaction rule reads as its default."""
    return [
        error_for_wrong_shape(
            f"{_PREFIX}[{key!r}]", expected, repr(type(scope[key]).__name__)
        )
        for key, (usable, expected) in _SHAPES.items()
        if key in scope and not usable(scope[key])
    ]


def _rule_problem(dotted: str) -> str | None:
    """Return why a `RULE` path names no callable, `None` when it names one."""
    try:
        target = import_string(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    return None if callable(target) else "names no callable"


def _rule_errors(value: object) -> list[CheckMessage]:
    """Report a `RULE` path that does not import or names no callable."""
    if not isinstance(value, str):
        return []
    problem = _rule_problem(value)
    if problem is None:
        return []
    return [
        error_for_unusable_seam(
            f"{_PREFIX}['RULE']",
            value,
            problem,
            "the transaction rule raises ImproperlyConfigured for every request it "
            "is asked about",
            "Name a callable taking the request and the view kind that returns the "
            "database aliases to wrap.",
        )
    ]


@register(NEXT)
def check_transactions_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the `TRANSACTIONS` keys, the value shapes and the `RULE` path."""
    scope = raw_scope(_SCOPE)
    if scope is None:
        return []
    errors = errors_for_unknown_keys(dict(scope), allowed=_KEYS, prefix=_PREFIX)
    errors.extend(_shape_errors(scope))
    errors.extend(_rule_errors(scope.get("RULE")))
    return errors


@register(NEXT)
def check_transactions_aliases(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about `TRANSACTIONS["ALIASES"]` entries `DATABASES` does not declare."""
    scope = raw_scope(_SCOPE)
    aliases = None if scope is None else scope.get("ALIASES")
    if not alias_list(aliases):
        return []
    declared = frozenset(connections)
    missing = [alias for alias in dict.fromkeys(aliases) if alias not in declared]
    if not missing:
        return []
    names = ", ".join(repr(alias) for alias in missing)
    return [
        DjangoWarning(
            f"{_PREFIX}['ALIASES'] names {names}, which DATABASES does not declare, "
            "so next.dj views open no transaction on it.",
            hint="Add the alias to DATABASES or remove it from ALIASES.",
            obj=settings,
            id="next.W179",
        )
    ]


@register(NEXT)
def check_atomic_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `page.atomic()` or an action `atomic` choice that applies nothing."""
    actions = diagnostics().invalid_atomic
    errors: list[CheckMessage] = [
        Error(
            f"page.atomic() in {file_path} declares no transaction, since {problem}. "
            "The page follows the method rule instead.",
            hint="Call page.atomic(True) or page.atomic(False) at the top of the "
            "module, with aliases= None, 'all' or a list of database aliases.",
            obj=settings,
            id="next.E240",
        )
        for file_path, declaration in atomic_declarations.loaded()
        if (problem := declaration.problem()) is not None
    ]
    errors.extend(
        Error(
            f"page.atomic() in {file_path} declares no transaction, since the call "
            "sits outside a page.py and so names no page.",
            hint="Call page.atomic() at the top of the page.py of the page it is for.",
            obj=settings,
            id="next.E240",
        )
        for file_path in atomic_declarations.outside()
    )
    errors.extend(
        Error(
            f"{owner} declares no transaction, since {problem}. The action follows "
            "the method rule instead.",
            hint="Use True, False or an AtomicOverride for @action(atomic=...) or "
            "the Meta.atomic of a form class.",
            obj=settings,
            id="next.E240",
        )
        for owner, problem in actions
    )
    return errors


@register(NEXT)
def check_atomic_requests(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that next.dj views ignore `ATOMIC_REQUESTS` and follow `TRANSACTIONS`."""
    aliases = [
        alias
        for alias, database in connections.settings.items()
        if database.get("ATOMIC_REQUESTS")
    ]
    if not aliases:
        return []
    names = ", ".join(repr(alias) for alias in aliases)
    return [
        DjangoWarning(
            f"ATOMIC_REQUESTS is set on {names}. next.dj views ignore it and open "
            f"the transactions {_PREFIX} asks for, while other Django views keep it.",
            hint="Drop ATOMIC_REQUESTS, or keep it for the other views and steer "
            "next.dj views through TRANSACTIONS, page.atomic(), @action(atomic=) or "
            "Meta.atomic.",
            obj=settings,
            id="next.W154",
        )
    ]


__all__ = [
    "check_atomic_declarations",
    "check_atomic_requests",
    "check_transactions_aliases",
    "check_transactions_settings",
]
