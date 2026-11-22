"""Resolution-context snapshot passed to providers during DI resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from .cache import DependencyCache


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest


RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    {"request", "form", "cleaned_data", "_cache", "_stack", "_context_data"}
)

RECEIVER_NAMES: Final[frozenset[str]] = frozenset({"self", "cls"})
"""Parameter names the resolver skips as a method receiver, so none is ever injected."""


@dataclass(slots=True, eq=False)
class ResolutionContext:
    """Per-call snapshot of the inputs available during dependency resolution.

    Not frozen, as a frozen `__init__` is slower on every resolve.
    """

    request: HttpRequest | None
    form: object | None
    url_kwargs: Mapping[str, Any]
    context_data: Mapping[str, Any]
    cache: DependencyCache
    stack: list[str] = field(default_factory=list)
    cleaned_data: Mapping[str, Any] | None = None

    @classmethod
    def for_page(
        cls,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        context_data: Mapping[str, Any] | None = None,
    ) -> ResolutionContext:
        """Return the context a page callable resolves against over the render cache.

        A URL kwarg named like a reserved key stays out, as the resolver claims those.
        """
        kept = (
            url_kwargs
            if RESERVED_KEYS.isdisjoint(url_kwargs)
            else {k: v for k, v in url_kwargs.items() if k not in RESERVED_KEYS}
        )
        data = {} if context_data is None else context_data
        return cls(request, None, kept, data, DependencyCache(dep_cache))
