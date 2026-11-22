"""System checks for the `TRANSACTIONS` scope and the transactions views declare."""

from __future__ import annotations

from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.db import connections

from next.checks import NEXT
from next.checks.common import (
    DeclaringCall,
    callable_problem,
    declaration_errors,
    raw_scope,
)
from next.conf.checks import error_for_misfit, error_for_unusable_path
from next.conf.shapes import misfits
from next.forms.checks.sources import diagnostics
from next.pages.loaders import PAGE_MODULE
from next.pages.transactions import atomic_store

from .policy import SHAPE, alias_list


_SCOPE: Final = "TRANSACTIONS"
_PREFIX: Final = f"NEXT_FRAMEWORK[{_SCOPE!r}]"


def _rule_errors(value: object) -> list[CheckMessage]:
    """Report a `RULE` path that does not import or names no callable."""
    if not isinstance(value, str):
        return []
    problem = callable_problem(value)
    if problem is None:
        return []
    return [
        error_for_unusable_path(
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
    errors = [error_for_misfit(_PREFIX, misfit) for misfit in misfits(scope, SHAPE)]
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


_ATOMIC_CALL: Final = DeclaringCall(
    "page.atomic()", "next.E240", PAGE_MODULE, "page", declares="no transaction"
)


@register(NEXT)
def check_atomic_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `page.atomic()` or an action `atomic` choice that applies nothing."""
    errors = declaration_errors(
        _ATOMIC_CALL,
        atomic_store,
        consequence="The page follows the method rule instead.",
        hint="Call page.atomic() with True, False or 'writes' at the top of the "
        "module, with aliases= None, 'all' or a list of database aliases.",
    )
    errors.extend(
        Error(
            f"{owner} declares no transaction, since {problem}. The action follows "
            "the method rule instead.",
            hint="Use True, False, 'writes' or an AtomicOverride for "
            "@action(atomic=...) or the Meta.atomic of a form class.",
            obj=settings,
            id="next.E240",
        )
        for owner, problem in diagnostics().invalid_atomic
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
