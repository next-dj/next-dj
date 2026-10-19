"""Walk of the root URLconf for where `next.urls` and each `pages()` tree are included.

Walking it, not a registry of `pages()` calls, follows a swapped URLconf and its prefix.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING, overload, override

from django.conf import settings
from django.core.exceptions import AppRegistryNotReady
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import translation


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.utils import PageRoot


@dataclass(frozen=True, slots=True)
class Mount:
    """An application page tree a `pages()` call mounts, read when its URLconf ran."""

    app: str
    name: str
    subdir: str
    path: Path
    namespace: str


class MountedPatterns(Sequence["URLPattern | URLResolver"]):
    """A lazy pattern sequence the walk records where it is included, never iterating.

    Iterating one builds page routes, and the walk also runs while they are being built.
    """

    mount: Mount | None = None

    @abstractmethod
    def _patterns(self) -> Sequence[URLPattern | URLResolver]:
        """Return the patterns, building them on the first read."""

    @override
    def __iter__(self) -> Iterator[URLPattern | URLResolver]:
        return iter(self._patterns())

    @override
    def __reversed__(self) -> Iterator[URLPattern | URLResolver]:
        return reversed(self._patterns())

    @override
    def __len__(self) -> int:
        return len(self._patterns())

    @overload
    def __getitem__(self, key: int, /) -> URLPattern | URLResolver: ...

    @overload
    def __getitem__(self, key: slice, /) -> list[URLPattern | URLResolver]: ...

    @override
    def __getitem__(
        self, key: int | slice, /
    ) -> URLPattern | URLResolver | list[URLPattern | URLResolver]:
        if isinstance(key, slice):
            return list(self._patterns()[key])
        return self._patterns()[key]


@dataclass(frozen=True, slots=True)
class MountPoint:
    """One `pages()` tree, the route the URLconf includes it under and its kwargs."""

    mount: Mount
    prefix: str
    kwargs: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class UrlconfMounts:
    """Where the root URLconf includes `next.urls` and every `pages()` tree.

    `include_index` counts the `pages()` trees listed ahead of the `next.urls` include.
    """

    include_point: str | None = None
    include_index: int = 0
    include_kwargs: frozenset[str] = frozenset()
    points: tuple[MountPoint, ...] = ()
    claimed: frozenset[tuple[str, str]] = frozenset()


NO_MOUNTS = UrlconfMounts()
"""The answer for a project without a root URLconf, which mounts no tree."""


def tree_route(root: PageRoot, include_point: str) -> str:
    """Return the route the patterns of `root` start at, from the URLconf root.

    A `pages()` tree carries it as its prefix, and any other starts at `include_point`.
    """
    return include_point if root.prefix is None else root.prefix


def outer_kwargs(root: PageRoot, mounts: UrlconfMounts) -> frozenset[str]:
    """Return the kwargs the URLconf captures around `root`, ahead of its own routes."""
    if root.prefix is None:
        return mounts.include_kwargs
    return next(
        (
            point.kwargs
            for point in mounts.points
            if point.prefix == root.prefix and point.mount.path == root.path
        ),
        frozenset(),
    )


def _captured(resolver: URLResolver) -> frozenset[str]:
    """Return the kwargs an include hands on, its captured groups and its extra ones."""
    return frozenset(resolver.pattern.regex.groupindex) | frozenset(
        resolver.default_kwargs
    )


def _join_route(head: str, tail: str) -> str:
    """Join two routes the way Django joins a resolver match, dropping a second `^`."""
    return tail if not head else head + tail.removeprefix("^")


class _Walk:
    """The state of one walk over the include tree of the root URLconf.

    `complete` turns false on a module still importing, so the walk is not kept.
    """

    def __init__(self) -> None:
        """Start with nothing found."""
        self.include_point: str | None = None
        self.include_index = 0
        self.include_kwargs: frozenset[str] = frozenset()
        self.points: list[MountPoint] = []
        self.complete = True

    def visit(
        self, resolver: URLResolver, prefix: str, kwargs: frozenset[str] = frozenset()
    ) -> None:
        """Record a mounted include target, or walk on into a list of patterns.

        Another lazy sequence is skipped, since building its routes may read the trees.
        """
        source = resolver.urlconf_module
        if isinstance(source, ModuleType) and not hasattr(source, "urlpatterns"):
            self.complete = False
            return
        patterns = getattr(source, "urlpatterns", source)
        if isinstance(patterns, MountedPatterns):
            self._note(patterns.mount, prefix, kwargs)
            return
        if not isinstance(patterns, list | tuple):
            return
        for entry in patterns:
            if isinstance(entry, URLResolver):
                self.visit(
                    entry,
                    _join_route(prefix, str(entry.pattern)),
                    kwargs | _captured(entry),
                )

    def _note(self, mount: Mount | None, prefix: str, kwargs: frozenset[str]) -> None:
        if mount is not None:
            self.points.append(MountPoint(mount, prefix, kwargs))
        elif self.include_point is None:
            self.include_point = prefix
            self.include_index = len(self.points)
            self.include_kwargs = kwargs

    def result(self) -> UrlconfMounts:
        """Return what the walk found."""
        return UrlconfMounts(
            include_point=self.include_point,
            include_index=self.include_index,
            include_kwargs=self.include_kwargs,
            points=tuple(self.points),
            claimed=frozenset(
                (point.mount.name, point.mount.subdir) for point in self.points
            ),
        )


class _WalkMemo:
    """The last walk, held beside the root resolver it read."""

    def __init__(self) -> None:
        """Start with no walk on record."""
        self.held: tuple[URLResolver, UrlconfMounts] | None = None


_memo = _WalkMemo()


def urlconf_mounts() -> UrlconfMounts:
    """Return where the root URLconf mounts page trees, walking it once per resolver.

    A new resolver is walked anew, and a locale prefix is read in `LANGUAGE_CODE`.
    """
    urlconf = getattr(settings, "ROOT_URLCONF", None)
    if urlconf is None:
        return NO_MOUNTS
    resolver = get_resolver(urlconf)
    held = _memo.held
    if held is not None and held[0] is resolver:
        return held[1]
    walk = _Walk()
    try:
        with translation.override(settings.LANGUAGE_CODE):
            walk.visit(resolver, "")
    except (ImportError, AppRegistryNotReady):
        return NO_MOUNTS
    found = walk.result()
    if walk.complete:
        _memo.held = (resolver, found)
    return found


__all__ = [
    "NO_MOUNTS",
    "Mount",
    "MountPoint",
    "MountedPatterns",
    "UrlconfMounts",
    "outer_kwargs",
    "tree_route",
    "urlconf_mounts",
]
