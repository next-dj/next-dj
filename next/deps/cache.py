"""Sentinels, the per-resolution cache and the request memo of DI resolution.

`_IN_PROGRESS` and `_CACHE_MISS` keep a `None`-valued hit from reading as a miss.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from next.requests import held_next, next_of


if TYPE_CHECKING:
    from collections.abc import Mapping

    from .scope import RequestScope


_IN_PROGRESS: object = object()
_CACHE_MISS: object = object()


def render_dep_cache(request: object | None) -> dict[str, Any]:
    """Return the form dispatch cache on `request`, or a new dict that stays off it.

    A cache stored on the request would be shared with every component on the page.
    """
    held = held_next(request)
    cache = None if held is None else held.dep_cache
    return {} if cache is None else cache


def bind_dep_cache(
    request: object, cache: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Hang the dispatch `cache` on `request` and return the one it replaces.

    Binding the returned cache restores it, and binding `None` clears the request.
    """
    namespace = next_of(request)
    previous = namespace.dep_cache
    namespace.dep_cache = cache
    return previous


class DependencyCache:
    """Store resolved dependency values and detect cycles via in-progress keys.

    The in-progress set is allocated on first use, as only a named `Depends` marks.
    """

    __slots__ = ("_cache", "_in_progress")

    def __init__(self, backing_dict: dict[str, Any] | None = None) -> None:
        """Initialise storage, optionally sharing an externally owned dict."""
        self._cache: dict[str, Any] = backing_dict if backing_dict is not None else {}
        self._in_progress: set[str] | None = None

    def get(self, key: str) -> object:
        """Return the cached value, `_IN_PROGRESS`, or `_CACHE_MISS`."""
        in_progress = self._in_progress
        if in_progress is not None and key in in_progress:
            return _IN_PROGRESS
        return self._cache.get(key, _CACHE_MISS)

    def set(self, key: str, value: object) -> None:
        """Store a finished resolution under the given key."""
        self._cache[key] = value
        if self._in_progress is not None:
            self._in_progress.discard(key)

    def mark_in_progress(self, key: str) -> None:
        """Mark the key as currently being resolved for cycle detection."""
        if self._in_progress is None:
            self._in_progress = set()
        self._in_progress.add(key)

    def unmark_in_progress(self, key: str) -> None:
        """Clear the in-progress marker for the key."""
        if self._in_progress is not None:
            self._in_progress.discard(key)

    def finished(self) -> Mapping[str, Any]:
        """Return a read-only view of the finished values, in-progress keys left out."""
        return MappingProxyType(self._cache)

    def __len__(self) -> int:
        """Return the number of stored values."""
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Return membership in the backing dict."""
        return key in self._cache


@dataclass(frozen=True, slots=True, eq=False)
class RequestMemo:
    """The request scope the async path shares keyed values through, and its policy.

    A key two consumers of one root name is shared, `shared` and `share_all` widen it.
    """

    scope: RequestScope
    shared: frozenset[str] = frozenset()
    share_all: bool = False
    unit: RequestScope | None = None

    def instance(self) -> RequestMemo:
        """Return the memo of one component instance, a fresh layer of the unit scope.

        Its props set it apart from siblings and parent, so neither reads its values.
        """
        unit = self.scope if self.unit is None else self.unit
        return RequestMemo(unit.layer(), self.shared, self.share_all, unit)


request_memo: ContextVar[RequestMemo | None] = ContextVar(
    "next_deps_request_memo", default=None
)
"""The memo of the current request, set by the owner of its scope."""
