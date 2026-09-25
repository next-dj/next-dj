"""`RouteSitemap`, the Django sitemap one page tree builds from its routes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlsplit

from django.contrib.sitemaps import Sitemap

from next.deps.resolver import current_resolver
from next.introspect import describe_callable
from next.pages import page
from next.pages.metadata import Robots, site_segment
from next.urls.reverse import page_reverse
from next.utils import walk_page_tree

from .errors import SeoBaseError, SitemapTrailError
from .markers import Entry
from .registry import sitemap_items_registry


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterable
    from datetime import date, datetime
    from pathlib import Path

    from django.contrib.sites.models import Site
    from django.contrib.sites.requests import RequestSite
    from django.http import HttpRequest

    from next.pages.metadata import Metadata

    from .discovery import SeoRoot


logger = logging.getLogger(__name__)

_DYNAMIC_MARK = "["
_DEFAULT_LIMIT = 50000


@dataclass(frozen=True, slots=True)
class SitemapItem:
    """One URL of a `RouteSitemap`, a trail with the kwargs that reverse it."""

    trail: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    lastmod: datetime | date | None = None
    changefreq: str | None = None
    priority: float | None = None

    @property
    def key(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Return the identity two items share when they reverse to one location."""
        return (
            self.trail,
            tuple(sorted((name, str(value)) for name, value in self.kwargs.items())),
        )


if TYPE_CHECKING:
    _SitemapBase = Sitemap[SitemapItem]
else:
    _SitemapBase = Sitemap
"""The stub declares the generic, the runtime class takes no parameter."""


def is_noindex(metadata: Metadata) -> bool:
    """Whether the static metadata of a page keeps it out of the index."""
    robots = metadata.robots
    if isinstance(robots, Robots):
        return robots.index is False
    return isinstance(robots, str) and "noindex" in robots


def _string_list(value: object) -> tuple[str, ...]:
    """Return the strings of a declared list, anything else reads as none."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _base_origin() -> tuple[str, str] | None:
    """Return the scheme and netloc of the site-wide `base`, when one is set."""
    base = site_segment().base
    if base is None:
        return None
    parts = urlsplit(base)
    return (parts.scheme, parts.netloc)


class RouteSitemap(_SitemapBase):
    """The sitemap of one page tree, static routes plus the `@sitemap.items` entries.

    One instance serves one request, so the item list is memoised on the instance.
    """

    def __init__(
        self,
        seo_root: SeoRoot,
        module: types.ModuleType,
        *,
        request: HttpRequest | None = None,
    ) -> None:
        """Read the Django sitemap attributes the module declares."""
        self.seo_root = seo_root
        self.request = request
        self.exclude = _string_list(getattr(module, "exclude", ()))
        self.default_changefreq = _as_str(getattr(module, "changefreq", None))
        self.default_priority = _as_priority(getattr(module, "priority", None))
        self.i18n = bool(getattr(module, "i18n", False))
        languages = getattr(module, "languages", None)
        self.languages = None if languages is None else list(_string_list(languages))
        self.alternates = bool(getattr(module, "alternates", False))
        self.x_default = bool(getattr(module, "x_default", False))
        self.protocol = _as_str(getattr(module, "protocol", None))
        self.limit = _as_limit(getattr(module, "limit", None))
        self._built: list[SitemapItem] | None = None

    @property
    def root(self) -> Path:
        """Return the page tree this sitemap covers."""
        return self.seo_root.path

    @override
    def items(self) -> list[SitemapItem]:
        """Return the static routes and the declared entries, built once."""
        items = self._built
        if items is None:
            items = self._built = self._build_items()
        return items

    def _build_items(self) -> list[SitemapItem]:
        walked = dict(walk_page_tree(self.root, self.seo_root.skip_names))
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        items: list[SitemapItem] = []
        for trail, page_path in walked.items():
            if _DYNAMIC_MARK in trail or self._excluded(trail):
                continue
            if is_noindex(page.static_metadata(page_path)):
                continue
            self._add(items, seen, SitemapItem(trail))
        for trail, func in sitemap_items_registry.entries_for(self.root):
            if trail not in walked:
                raise SitemapTrailError(self.root, trail)
            for entry in self._entries_of(func):
                self._add(
                    items,
                    seen,
                    SitemapItem(
                        trail,
                        entry.kwargs,
                        entry.lastmod,
                        entry.changefreq,
                        entry.priority,
                    ),
                )
        return items

    def _excluded(self, trail: str) -> bool:
        return any(fnmatch(trail, pattern) for pattern in self.exclude)

    def _add(
        self,
        items: list[SitemapItem],
        seen: set[tuple[str, tuple[tuple[str, str], ...]]],
        item: SitemapItem,
    ) -> None:
        key = item.key
        if key in seen:
            logger.warning(
                "the sitemap of %s lists %r with %r twice, the later entry is dropped",
                self.root,
                item.trail,
                dict(item.kwargs),
            )
            return
        seen.add(key)
        items.append(item)

    def _entries_of(self, func: Callable[..., Any]) -> Iterable[Entry]:
        """Call one items callable through the resolver and normalise what it yields."""
        resolved = current_resolver().resolve_dependencies(func, request=self.request)
        for value in func(**resolved):
            if isinstance(value, Entry):
                yield value
            elif isinstance(value, Mapping):
                yield Entry(kwargs=value)
            else:
                msg = (
                    f"{describe_callable(func)} yielded {type(value).__name__} "
                    "instead of a next.seo.Entry or a mapping of URL kwargs"
                )
                raise TypeError(msg)

    @override
    def location(self, item: SitemapItem) -> str:
        """Reverse the item lazily, so an active language prefix lands in the path."""
        return page_reverse(item.trail, **item.kwargs)

    def lastmod(self, item: SitemapItem) -> datetime | date | None:
        """Return the modification time the entry carries."""
        return item.lastmod

    def changefreq(self, item: SitemapItem) -> str | None:
        """Return the item value ahead of the module default."""
        return item.changefreq or self.default_changefreq

    def priority(self, item: SitemapItem) -> float | None:
        """Return the item value ahead of the module default."""
        return self.default_priority if item.priority is None else item.priority

    @override
    def get_protocol(self, protocol: str | None = None) -> str:
        """Prefer the declared protocol, then the `base` scheme, then the request."""
        origin = _base_origin()
        base_scheme = None if origin is None else origin[0]
        return self.protocol or base_scheme or protocol or "https"

    @override
    def get_domain(self, site: Site | RequestSite | None = None) -> str:
        """Prefer the `base` netloc, then the site the request derived."""
        origin = _base_origin()
        if origin is not None:
            return origin[1]
        if site is None:
            raise SeoBaseError(self.root)
        domain: str = site.domain
        return domain


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_priority(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_limit(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else _DEFAULT_LIMIT


__all__ = ["RouteSitemap", "SitemapItem", "is_noindex"]
