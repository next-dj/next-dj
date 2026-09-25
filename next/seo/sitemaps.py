"""`RouteSitemap`, the Django sitemap one page tree builds from its routes."""

from __future__ import annotations

import datetime
import functools
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, override
from urllib.parse import urlsplit

from django.contrib.sitemaps import Sitemap
from django.utils import timezone

from next.caches import DEFAULT_CACHE_SIZE
from next.deps.resolver import current_resolver
from next.introspect import describe_callable
from next.pages import page
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.metadata import metadata_options, page_noindex, site_segment
from next.urls.reverse import page_reverse
from next.utils import is_dynamic_trail, is_int

from .errors import SitemapOriginError, SitemapTrailError
from .markers import Entry


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from django.contrib.sites.models import Site
    from django.contrib.sites.requests import RequestSite
    from django.http import HttpRequest

    from .discovery import SeoRoot


logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50000
_GLOB_WILDCARDS: Final = {"*": ".*", "?": "."}

type ItemKey = tuple[str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class SitemapItem:
    """One URL of a `RouteSitemap`, a trail with the kwargs that reverse it."""

    trail: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    lastmod: datetime.datetime | datetime.date | None = None
    changefreq: str | None = None
    priority: float | None = None

    @property
    def key(self) -> ItemKey:
        """Return the identity two items share when they reverse to one location."""
        return (
            self.trail,
            tuple(sorted((name, str(value)) for name, value in self.kwargs.items())),
        )


if TYPE_CHECKING:
    _SitemapBase = Sitemap[SitemapItem]
else:
    _SitemapBase = Sitemap


def _string_list(value: object) -> tuple[str, ...]:
    """Return the strings of a declared list, anything else reads as none."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _string(value: object) -> str | None:
    """Return a declared non-empty string, anything else reads as none."""
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class SitemapOptions:
    """The attributes a `sitemap.py` declares, a wrong shape read as unset."""

    changefreq: str | None = None
    priority: float | None = None
    limit: int = _DEFAULT_LIMIT
    cache: int | None = None
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] | None = None
    i18n: bool = False
    alternates: bool = False
    x_default: bool = False
    protocol: str | None = None

    @classmethod
    def read(cls, module: types.ModuleType) -> SitemapOptions:
        """Read the attributes of `module`, the checks report the wrong shapes."""
        priority = getattr(module, "priority", None)
        limit = getattr(module, "limit", None)
        cache = getattr(module, "cache", None)
        languages = getattr(module, "languages", None)
        return cls(
            exclude=_string_list(getattr(module, "exclude", None)),
            changefreq=_string(getattr(module, "changefreq", None)),
            priority=float(priority)
            if is_int(priority) or isinstance(priority, float)
            else None,
            limit=limit if is_int(limit) and limit > 0 else _DEFAULT_LIMIT,
            cache=cache if is_int(cache) and cache > 0 else None,
            i18n=bool(getattr(module, "i18n", None)),
            languages=None if languages is None else _string_list(languages),
            alternates=bool(getattr(module, "alternates", None)),
            x_default=bool(getattr(module, "x_default", None)),
            protocol=_string(getattr(module, "protocol", None)),
        )


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def _glob_pattern(glob: str) -> re.Pattern[str]:
    """Compile an `exclude` glob, where brackets are literal since trails use them."""
    return re.compile(
        "".join(_GLOB_WILDCARDS.get(char) or re.escape(char) for char in glob)
    )


def is_excluded(trail: str, exclude: Sequence[str]) -> bool:
    """Whether one of the `exclude` globs of a `sitemap.py` covers `trail`.

    Only `*` and `?` are wildcards, so `posts/[slug]` names that one dynamic trail.
    """
    return any(_glob_pattern(glob).fullmatch(trail) for glob in exclude)


def lastmod_datetime(value: datetime.datetime | datetime.date) -> datetime.datetime:
    """Return a `lastmod` as an aware datetime, so dates and datetimes compare.

    A date or a naive value reads in the current time zone, keeping the day declared.
    """
    if not isinstance(value, datetime.datetime):
        value = datetime.datetime.combine(value, datetime.time.min)
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def static_noindex(page_path: Path) -> bool:
    """Whether the static metadata of a page keeps it out of the index.

    A chain the schema refuses reads as indexed with a warning, the checks report it.
    """
    try:
        return page_noindex(page.static_metadata(page_path))
    except (PageMetadataShapeError, PageMetadataConflictError) as exc:
        logger.warning(
            "the metadata of %s is refused (%s), so it reads as indexed", page_path, exc
        )
        return False


def listed_trails(trails: Mapping[str, Path], exclude: Sequence[str]) -> list[str]:
    """List the static trails a sitemap carries, dropping excluded and noindex pages."""
    return [
        trail
        for trail, page_path in trails.items()
        if not is_dynamic_trail(trail)
        and not is_excluded(trail, exclude)
        and not static_noindex(page_path)
    ]


def serves_sitemap(roots: Iterable[SeoRoot]) -> bool:
    """Whether a `sitemap.py` imported and `NOINDEX` leaves the sitemap served."""
    if metadata_options().noindex:
        return False
    return any(root.sitemap_module is not None for root in roots)


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
        options = SitemapOptions.read(module)
        self.seo_root = seo_root
        self.request = request
        self.exclude = options.exclude
        self.default_changefreq = options.changefreq
        self.default_priority = options.priority
        self.i18n = options.i18n
        languages = options.languages
        self.languages = None if languages is None else list(languages)
        self.alternates = options.alternates
        self.x_default = options.x_default
        self.protocol = options.protocol
        self.limit = options.limit
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
        """List the static routes, then the declared entries in their place.

        An entry for a static trail replaces the bare route, keeping its hints.
        """
        walked = self.seo_root.trails
        items: dict[ItemKey, SitemapItem] = {}
        for trail in listed_trails(walked, self.exclude):
            item = SitemapItem(trail)
            items[item.key] = item
        declared: set[ItemKey] = set()
        for trail, func in self.seo_root.items_entries():
            if trail not in walked:
                raise SitemapTrailError(self.seo_root.sitemap_path, trail)
            for entry in self._entries_of(func):
                item = SitemapItem(
                    trail, entry.kwargs, entry.lastmod, entry.changefreq, entry.priority
                )
                if item.key in declared:
                    logger.warning(
                        "the sitemap of %s lists %r with %r twice, the later "
                        "entry is dropped",
                        self.root,
                        trail,
                        dict(item.kwargs),
                    )
                    continue
                declared.add(item.key)
                items[item.key] = item
        return list(items.values())

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

    def lastmod(self, item: SitemapItem) -> datetime.datetime | None:
        """Return the modification time the entry carries, as an aware datetime."""
        return None if item.lastmod is None else lastmod_datetime(item.lastmod)

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
            raise SitemapOriginError(self.root)
        domain: str = site.domain
        return domain


__all__ = [
    "RouteSitemap",
    "SitemapItem",
    "SitemapOptions",
    "is_excluded",
    "lastmod_datetime",
    "listed_trails",
    "serves_sitemap",
    "static_noindex",
]
