"""The addresses and names of the SEO routes and the lazy set a source backs.

A route without its source stays out, so a project view at that address still answers.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, overload, override

from next.diagnostics import FailureLog

from .manager import seo_manager


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from django.urls import URLPattern


SITEMAP_ROUTE: Final = "sitemap.xml"
SECTION_ROUTE: Final = "sitemap-<slug:section>.xml"
ROBOTS_ROUTE: Final = "robots.txt"

SITEMAP_NAME: Final = "sitemap"
SECTION_NAME: Final = "sitemap_section"
ROBOTS_NAME: Final = "robots"

DEFAULT_NAMESPACE: Final = "next"
"""The namespace the routes answer under when `next.urls` serves them."""

HOST_ROOT_NAMESPACE: Final = "next_seo"
"""The namespace of `next.seo.urls`, mounted at the host root."""


logger = logging.getLogger(__name__)
_failures = FailureLog(logger)


def _served(route: str, probe: Callable[[], bool]) -> bool:
    """Whether `probe` routes `route`, a probe that raises keeping the route.

    It runs while every URL resolves, so a failure must not take the other routes
    down, and the view behind the route answers 503 on the same failure.
    """
    try:
        return probe()
    except Exception:
        if _failures.first_failure(route):
            logger.exception(
                "Deciding whether to route /%s raised, so the route stays and "
                "answers 503 until its source loads.",
                route,
            )
        return True


def served_names() -> frozenset[str]:
    """Return the names of the routes whose source a page tree or a backend backs."""
    names: set[str] = set()
    if _served(SITEMAP_ROUTE, seo_manager.serves_sitemap):
        names.update((SITEMAP_NAME, SECTION_NAME))
    if _served(ROBOTS_ROUTE, lambda: seo_manager.robots_source() is not None):
        names.add(ROBOTS_NAME)
    return frozenset(names)


def served_patterns(patterns: Iterable[URLPattern]) -> list[URLPattern]:
    """Return the patterns among `patterns` whose source is there."""
    names = served_names()
    return [pattern for pattern in patterns if pattern.name in names]


class SeoPatterns(Sequence["URLPattern"]):
    """The SEO routes filtered by their sources, refiltered when the manager resets."""

    def __init__(self, patterns: Iterable[URLPattern]) -> None:
        """Hold every route, filtering none yet."""
        self.patterns = tuple(patterns)
        self._held: tuple[int, tuple[URLPattern, ...]] | None = None

    def _served(self) -> tuple[URLPattern, ...]:
        seo_manager.refresh()
        version = seo_manager.version
        held = self._held
        if held is not None and held[0] == version:
            return held[1]
        served = tuple(served_patterns(self.patterns))
        self._held = (version, served)
        return served

    @override
    def __iter__(self) -> Iterator[URLPattern]:
        return iter(self._served())

    @override
    def __len__(self) -> int:
        return len(self._served())

    @overload
    def __getitem__(self, key: int, /) -> URLPattern: ...

    @overload
    def __getitem__(self, key: slice, /) -> tuple[URLPattern, ...]: ...

    @override
    def __getitem__(self, key: int | slice, /) -> URLPattern | tuple[URLPattern, ...]:
        return self._served()[key]


__all__ = [
    "DEFAULT_NAMESPACE",
    "HOST_ROOT_NAMESPACE",
    "ROBOTS_NAME",
    "ROBOTS_ROUTE",
    "SECTION_NAME",
    "SECTION_ROUTE",
    "SITEMAP_NAME",
    "SITEMAP_ROUTE",
    "SeoPatterns",
    "served_names",
    "served_patterns",
]
