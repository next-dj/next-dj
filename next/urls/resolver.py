"""Trie-backed URL resolver that narrows resolve() to a few candidates.

The file router owns its whole subtree, so the route list is a ready-made
trie. Candidates for a path are picked in O(depth) from a static route map
plus a segment trie, then tried in original pattern order, which keeps
Django's first-match-wins semantics for overlapping routes.
"""

from __future__ import annotations

import operator
import re
from typing import TYPE_CHECKING, Any, cast, override

from django.urls import Resolver404, URLPattern, URLResolver
from django.urls.resolvers import ResolverMatch, RoutePattern


if TYPE_CHECKING:
    from collections.abc import Iterable


type _Candidate = tuple[int, URLPattern | URLResolver]
type _Tried = list[list[URLPattern | URLResolver]]

_PARAM_RE = re.compile(r"<(?:([^>:]+):)?[^>]+>")
_SINGLE_SEGMENT_CONVERTERS = frozenset({"int", "slug", "str", "uuid"})
_by_index = operator.itemgetter(0)


def _is_single_segment(segment: str) -> bool:
    """Tell whether every converter in the segment matches within one segment."""
    converters = _PARAM_RE.findall(segment)
    return all((name or "str") in _SINGLE_SEGMENT_CONVERTERS for name in converters)


class _TrieNode:
    """Trie node with literal children, one param edge, and terminals."""

    __slots__ = ("literals", "param", "tail_terminals", "terminals")

    def __init__(self) -> None:
        self.literals: dict[str, _TrieNode] = {}
        self.param: _TrieNode | None = None
        self.terminals: list[_Candidate] = []
        self.tail_terminals: list[_Candidate] = []


def _collect(
    node: _TrieNode, segments: list[str], depth: int, found: list[_Candidate]
) -> None:
    found.extend(node.tail_terminals)
    if depth == len(segments):
        found.extend(node.terminals)
        return
    segment = segments[depth]
    literal_child = node.literals.get(segment)
    if literal_child is not None:
        _collect(literal_child, segments, depth + 1, found)
    if node.param is not None and segment:
        _collect(node.param, segments, depth + 1, found)


class _RouteIndex:
    """Static route map plus a segment trie for parameterised routes."""

    __slots__ = ("root", "static", "unindexed")

    def __init__(self, patterns: Iterable[URLPattern | URLResolver]) -> None:
        self.static: dict[str, _Candidate] = {}
        self.root = _TrieNode()
        self.unindexed: list[_Candidate] = []
        for index, pattern in enumerate(patterns):
            self._add((index, pattern))

    def _add(self, candidate: _Candidate) -> None:
        pattern = candidate[1]
        if not isinstance(pattern, URLPattern) or not isinstance(
            pattern.pattern, RoutePattern
        ):
            self.unindexed.append(candidate)
            return
        route = str(pattern.pattern)
        if "<" not in route:
            self.static.setdefault(route, candidate)
            return
        node = self.root
        for segment in route.split("/"):
            if "<" not in segment:
                node = node.literals.setdefault(segment, _TrieNode())
            elif _is_single_segment(segment):
                if node.param is None:
                    node.param = _TrieNode()
                node = node.param
            else:
                # `<path:...>` and custom converters can span "/", so the
                # candidate must match any remaining tail from this node.
                node.tail_terminals.append(candidate)
                return
        node.terminals.append(candidate)

    def candidates(self, path: str) -> list[_Candidate]:
        found = list(self.unindexed)
        static_hit = self.static.get(path)
        if static_hit is not None:
            found.append(static_hit)
        _collect(self.root, path.split("/"), 0, found)
        found.sort(key=_by_index)
        return found


def _extend_tried(
    tried: _Tried,
    pattern: URLPattern | URLResolver,
    sub_tried: list[list[URLPattern | URLResolver]] | None = None,
) -> None:
    # Mirrors URLResolver._extend_tried, which django-stubs does not expose.
    if sub_tried is None:
        tried.append([pattern])
    else:
        tried.extend([pattern, *t] for t in sub_tried)


def _join_route(route1: str, route2: str) -> str:
    # Mirrors URLResolver._join_route, which django-stubs does not expose.
    if not route1:
        return route2
    return route1 + route2.removeprefix("^")


class TrieURLResolver(URLResolver):
    """URLResolver that dispatches resolve() through a route trie.

    The index is rebuilt whenever the `version_token()` of the wrapped
    patterns changes. Any miss falls back to the inherited linear scan
    for the canonical `Resolver404` with a complete `tried` list.
    """

    _index_cache: tuple[tuple[int, int], _RouteIndex] | None = None

    def _current_token(self) -> tuple[int, int]:
        source = getattr(self.url_patterns, "version_token", None)
        if source is None:
            return (0, 0)
        return cast("tuple[int, int]", source())

    def _route_index(self) -> _RouteIndex:
        cached = self._index_cache
        if cached is not None and cached[0] == self._current_token():
            return cached[1]
        index = _RouteIndex(self.url_patterns)
        # The token is read after the build because materialising lazy
        # patterns can register form actions and bump their version.
        self._index_cache = (self._current_token(), index)
        return index

    @override
    def resolve(self, path: str) -> ResolverMatch:
        """Resolve via trie candidates with a linear-scan fallback."""
        path = str(path)
        match = self.pattern.match(path)
        if match is not None:
            new_path, args, kwargs = match
            tried: _Tried = []
            for _, pattern in self._route_index().candidates(new_path):
                try:
                    sub_match = pattern.resolve(new_path)
                except Resolver404 as exc:
                    _extend_tried(tried, pattern, exc.args[0].get("tried"))
                    continue
                if sub_match is None:
                    tried.append([pattern])
                    continue
                return self._wrap_sub_match(pattern, sub_match, (args, kwargs), tried)
        return super().resolve(path)

    def _wrap_sub_match(
        self,
        pattern: URLPattern | URLResolver,
        sub_match: ResolverMatch,
        outer: tuple[tuple[Any, ...], dict[str, Any]],
        tried: _Tried,
    ) -> ResolverMatch:
        """Wrap a candidate sub-match the same way URLResolver.resolve does."""
        args, kwargs = outer
        sub_match_dict = {**kwargs, **self.default_kwargs}
        sub_match_dict.update(sub_match.kwargs)
        sub_match_args = sub_match.args
        if not sub_match_dict:
            sub_match_args = args + sub_match.args
        current_route = "" if isinstance(pattern, URLPattern) else str(pattern.pattern)
        _extend_tried(tried, pattern, sub_match.tried)
        return ResolverMatch(
            sub_match.func,
            sub_match_args,
            sub_match_dict,
            sub_match.url_name,
            [self.app_name, *sub_match.app_names],
            [self.namespace, *sub_match.namespaces],
            _join_route(current_route, sub_match.route),
            tried,
            # django-stubs omits these two ResolverMatch attributes.
            captured_kwargs=getattr(sub_match, "captured_kwargs", None),
            extra_kwargs={
                **self.default_kwargs,
                **getattr(sub_match, "extra_kwargs", {}),
            },
        )


__all__ = ["TrieURLResolver"]
