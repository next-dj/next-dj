"""Resolution-context snapshot passed to providers during DI resolution.

`ResolutionContext` collects request, form, URL kwargs, and template
context data into a single immutable view. Providers read from this
object without mutating it. `RESERVED_KEYS` lists the kwarg names that
`DependencyResolver.resolve_dependencies` treats as fixed inputs rather
than URL kwargs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest

    from .cache import DependencyCache


RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    {"request", "form", "_cache", "_stack", "_context_data"}
)


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    """Immutable snapshot of the inputs available during dependency resolution."""

    request: HttpRequest | None
    form: object | None
    url_kwargs: Mapping[str, Any]
    context_data: Mapping[str, Any]
    cache: DependencyCache
    stack: list[str] = field(default_factory=list)
