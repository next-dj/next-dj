"""System checks for the `TRANSACTIONS` scope and the transactions views declare."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning
from django.db import connections

from next.checks import NEXT, register
from next.checks.common import (
    DeclaringCall,
    callable_problem,
    misplaced_errors,
    raw_scope,
)
from next.conf.checks import error_for_misfit, error_for_unusable_path
from next.conf.defaults import AUTO
from next.conf.shapes import misfits
from next.conf.transactions import SCOPE, SHAPE, alias_list
from next.forms.checks.sources import diagnostics
from next.pages.loaders import PAGE_MODULE
from next.pages.transactions import transaction_declaration

from .policy import atomic_requests


if TYPE_CHECKING:
    from collections.abc import Mapping


_PREFIX: Final = f"NEXT_FRAMEWORK[{SCOPE!r}]"


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
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    errors = [error_for_misfit(_PREFIX, misfit) for misfit in misfits(scope, SHAPE)]
    errors.extend(_rule_errors(scope.get("RULE")))
    return errors


@register(NEXT)
def check_transactions_aliases(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about `TRANSACTIONS["ALIASES"]` entries `DATABASES` does not declare."""
    scope = raw_scope(SCOPE)
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


_TRANSACTION_CALL: Final = DeclaringCall(
    "page.transaction()", "next.E240", PAGE_MODULE, "page", declares="no transaction"
)


@register(NEXT)
def check_atomic_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a misplaced `page.transaction()` or a no-op action `atomic`."""
    errors = misplaced_errors(_TRANSACTION_CALL, transaction_declaration.outside())
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


def _replaced(scope: Mapping[str, object]) -> list[str]:
    """Return the `TRANSACTIONS` keys that stop following `ATOMIC_REQUESTS`."""
    replaced = [key for key in ("SAFE", "UNSAFE") if scope.get(key, AUTO) != AUTO]
    if scope.get("RULE") is not None:
        replaced.append("RULE")
    return replaced


@register(NEXT)
def check_atomic_requests(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that a `TRANSACTIONS` choice replaces `ATOMIC_REQUESTS` in next.dj views."""
    aliases = atomic_requests(connections.settings)
    scope = raw_scope(SCOPE)
    replaced = [] if scope is None else _replaced(scope)
    if not aliases or not replaced:
        return []
    names = ", ".join(repr(alias) for alias in aliases)
    keys = ", ".join(replaced)
    return [
        DjangoWarning(
            f"ATOMIC_REQUESTS is set on {names}, but {_PREFIX} sets {keys}, so next.dj "
            "views open the transactions it asks for, while other Django views keep "
            "ATOMIC_REQUESTS.",
            hint="Leave SAFE and UNSAFE at 'auto' and RULE at None to follow "
            "ATOMIC_REQUESTS, or drop ATOMIC_REQUESTS.",
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
