"""Resolution-context snapshot passed to providers during DI resolution.

`RESERVED_KEYS` lists the kwarg names that `DependencyResolver` treats as fixed
inputs rather than URL kwargs, and that the name-based providers refuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest

    from .cache import DependencyCache


RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    {"request", "form", "cleaned_data", "_cache", "_stack", "_context_data"}
)


@dataclass(slots=True, eq=False)
class ResolutionContext:
    """Per-call snapshot of the inputs available during dependency resolution.

    Not frozen, because a frozen `__init__` is measurably slower on every
    resolve while the mutable `stack` and `cache` it already carries make a
    frozen guarantee hollow anyway.
    """

    request: HttpRequest | None
    form: object | None
    url_kwargs: Mapping[str, Any]
    context_data: Mapping[str, Any]
    cache: DependencyCache
    stack: list[str] = field(default_factory=list)
    cleaned_data: Mapping[str, Any] | None = None
