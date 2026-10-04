"""`PageTreeSitemap`, the Django sitemap built from the routes and items of a tree.

The items are read lazily, so a sitemap page reads only the rows it lists.
"""

from __future__ import annotations

import datetime
import functools
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, NamedTuple, override

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.core.paginator import Paginator
from django.db.models import Max, QuerySet
from django.urls import NoReverseMatch
from django.utils import timezone, translation

from next.caches import DEFAULT_CACHE_SIZE
from next.deps.resolver import current_resolver
from next.diagnostics import FailureLog
from next.introspect import describe_callable
from next.pages import page
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.metadata.hreflang import x_default_url
from next.pages.metadata.normalize import X_DEFAULT
from next.urls.reverse import page_reverse
from next.utils import is_dynamic_trail, is_int

from .discovery import declared_cache
from .errors import SitemapTrailError
from .markers import SitemapEntry, is_number
from .origin import request_origin
from .pagination import ChainedEntries, LanguagePairs, Part


if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from pathlib import Path

    from django.contrib.sites.models import Site
    from django.contrib.sites.requests import RequestSite
    from django.http import HttpRequest

    from next.pages.responses import CacheControl

    from .discovery import SeoRoot
    from .pagination import Rows
    from .registry import SitemapItemsEntry


logger = logging.getLogger(__name__)
_refusals: Final = FailureLog(logger)

MAX_LIMIT: Final = 50000
"""The most URLs one sitemap document may list, by the sitemap protocol."""

_GLOB_WILDCARDS: Final = {"*": ".*", "?": "."}


class SitemapItem(NamedTuple):
    """One URL of a `PageTreeSitemap`, as a trail and the entry it reverses with."""

    trail: str
    entry: SitemapEntry


if TYPE_CHECKING:
    _SitemapBase = Sitemap[SitemapItem]
else:
    _SitemapBase = Sitemap


def _string_list(value: object) -> tuple[str, ...]:
    """Return the strings of a declared list or tuple, or an empty tuple."""
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _string(value: object) -> str | None:
    """Return `value` when it is a non-empty string, else `None`."""
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class SitemapOptions:
    """The attributes a `sitemap.py` declares, an invalid value read as unset."""

    changefreq: str | None = None
    priority: float | None = None
    limit: int = MAX_LIMIT
    cache: CacheControl | None = None
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] | None = None
    i18n: bool = False
    alternates: bool = False
    x_default: bool = False
    protocol: str | None = None

    @classmethod
    def read(cls, module: types.ModuleType) -> SitemapOptions:
        """Read the attributes of `module`, leaving invalid values to `next.E113`."""
        priority = getattr(module, "priority", None)
        limit = getattr(module, "limit", None)
        languages = getattr(module, "languages", None)
        return cls(
            exclude=_string_list(getattr(module, "exclude", None)),
            changefreq=_string(getattr(module, "changefreq", None)),
            priority=float(priority) if is_number(priority) else None,
            limit=min(limit, MAX_LIMIT) if is_int(limit) and limit > 0 else MAX_LIMIT,
            cache=declared_cache(module),
            i18n=bool(getattr(module, "i18n", None)),
            languages=None if languages is None else _string_list(languages),
            alternates=bool(getattr(module, "alternates", None)),
            x_default=bool(getattr(module, "x_default", None)),
            protocol=_string(getattr(module, "protocol", None)),
        )


def sitemap_languages(options: SitemapOptions) -> list[str]:
    """Return the declared `languages`, every code of `LANGUAGES` without them."""
    if options.languages is not None:
        return list(options.languages)
    return [code for code, _name in settings.LANGUAGES]


def effective_limit(options: SitemapOptions, languages: Sequence[str]) -> int:
    """Return how many URLs one page lists, reduced while URLs carry alternates.

    Each URL then links every language and `x-default`, so a page of 50000 URLs
    could exceed the 50 MB limit of a sitemap document.
    """
    if not (options.i18n and options.alternates):
        return options.limit
    links = len(languages) + int(options.x_default) + 1
    return max(1, min(options.limit, MAX_LIMIT // links))


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


def lastmod_datetime(value: datetime.date) -> datetime.datetime:
    """Return a `lastmod` as an aware datetime, so dates and datetimes compare.

    A date or a naive datetime is read in the current time zone.
    """
    if not isinstance(value, datetime.datetime):
        value = datetime.datetime.combine(value, datetime.time.min)
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value


def static_noindex(page_path: Path) -> bool:
    """Whether the static metadata of a page sets noindex.

    Metadata the schema rejects reads as indexed, with one warning per file version.
    """
    try:
        return page.static_metadata(page_path).noindex
    except (PageMetadataShapeError, PageMetadataConflictError) as exc:
        _refusals.warn(
            (page_path, _mtime(page_path)),
            "The metadata of %s is refused (%s), so the sitemap reads it as indexed. "
            "Run manage.py check to see what to fix.",
            page_path,
            exc,
        )
        return False


def _mtime(page_path: Path) -> float | None:
    """Return the modification time of `page_path`, `None` when it is missing."""
    try:
        return page_path.stat().st_mtime
    except OSError:
        return None


def listed_trails(trails: Mapping[str, Path], exclude: Sequence[str]) -> list[str]:
    """Return the static trails a sitemap lists, without excluded and noindex pages."""
    return [
        trail
        for trail, page_path in trails.items()
        if not is_dynamic_trail(trail)
        and not is_excluded(trail, exclude)
        and not static_noindex(page_path)
    ]


def _rows(value: object, func: Callable[..., Any]) -> Rows:
    """Return the value an items callable returned as sliceable rows.

    An unordered `QuerySet` stays lazy and is ordered by primary key, and an
    iterator is read into a list.
    """
    if isinstance(value, QuerySet):
        if value.ordered:
            return value
        if value.query.is_sliced:
            msg = (
                f"{describe_callable(func)} answered a sliced QuerySet with no order, "
                "which pages differently on every query. Order it before the slice."
            )
            raise TypeError(msg)
        return value.order_by("pk")
    if isinstance(value, str | bytes | Mapping) or not isinstance(value, Iterable):
        msg = (
            f"{describe_callable(func)} answered {type(value).__name__} instead of "
            "a sequence, a QuerySet or an iterator of rows"
        )
        raise TypeError(msg)
    return value if isinstance(value, Sequence) else list(value)


def _stamp(row: object, field: str | None) -> datetime.date | None:
    """Return the `lastmod` field of one row, when the declaration names one."""
    if field is None:
        return None
    value = row.get(field) if isinstance(row, Mapping) else getattr(row, field, None)
    return value if isinstance(value, datetime.date) else None


def _items_note(entry: SitemapItemsEntry, what: str) -> str:
    """Return the exception note naming the failing `@sitemap.items` declaration."""
    return (
        f"Raised by {what} of @sitemap.items({entry.trail!r}) on "
        f"{describe_callable(entry.func)}."
    )


def _same(item: SitemapItem) -> SitemapItem:
    return item


def _converter(entry: SitemapItemsEntry) -> Callable[[object], SitemapItem]:
    """Return the function converting one row of an items callable to an item."""

    def convert(row: object) -> SitemapItem:
        if isinstance(row, SitemapEntry):
            return SitemapItem(entry.trail, row)
        if entry.kwargs is not None:
            try:
                kwargs = entry.kwargs(row)
            except Exception as exc:
                exc.add_note(_items_note(entry, "the kwargs= callable"))
                raise
        elif isinstance(row, Mapping):
            kwargs = row
        else:
            msg = (
                f"{describe_callable(entry.func)} listed {type(row).__name__}, "
                "which reverses only through kwargs= on @sitemap.items. Pass it, or "
                "list next.seo.SitemapEntry values or mappings of URL kwargs."
            )
            raise TypeError(msg)
        return SitemapItem(
            entry.trail, SitemapEntry(kwargs=kwargs, lastmod=_stamp(row, entry.lastmod))
        )

    return convert


class PageTreeSitemap(_SitemapBase):
    """One sitemap section of a page tree, static routes first, then declared items.

    One instance serves one request, so the parts are built once per instance.
    """

    def __init__(
        self,
        seo_root: SeoRoot,
        options: SitemapOptions,
        *,
        static: Sequence[str] = (),
        items: Sequence[SitemapItemsEntry] = (),
        request: HttpRequest | None = None,
    ) -> None:
        """Store the routes and the declarations without calling an items callable."""
        self.seo_root = seo_root
        self.options = options
        self.request = request
        self.protocol = options.protocol
        self.i18n = options.i18n
        self.languages = None if options.languages is None else list(options.languages)
        self.limit = effective_limit(options, self.language_codes())
        self.alternates = options.alternates
        # Django strips the language prefix from its x-default URL, while the page
        # head names the URL of the default language, so `get_urls` adds that one.
        self.x_default = False
        self._static = tuple(static)
        self._declared = tuple(items)
        self._entries: ChainedEntries[SitemapItem] | None = None

    def entries(self) -> ChainedEntries[SitemapItem]:
        """Return the static routes and every declared part, built on the first call."""
        entries = self._entries
        if entries is None:
            static = [SitemapItem(trail, SitemapEntry()) for trail in self._static]
            parts = [Part(static, _same), *map(self._part, self._declared)]
            entries = self._entries = ChainedEntries(parts)
        return entries

    def _part(self, entry: SitemapItemsEntry) -> Part[SitemapItem]:
        """Call one items callable through the dependency resolver and wrap its rows."""
        if entry.trail not in self.seo_root.trails:
            raise SitemapTrailError(self.seo_root.sitemap_path, entry.trail)
        try:
            resolved = current_resolver().resolve_dependencies(
                entry.func, request=self.request
            )
            rows = entry.func(**resolved)
        except Exception as exc:
            exc.add_note(_items_note(entry, "the items callable"))
            raise
        return Part(
            _rows(rows, entry.func), _converter(entry), lastmod_field=entry.lastmod
        )

    @override
    def items(self) -> ChainedEntries[SitemapItem]:
        """Return every item as one lazy sequence."""
        return self.entries()

    @property
    @override
    def paginator(self) -> Paginator:
        """Paginate the lazy items, paired with the languages under `i18n`."""
        entries = self.entries()
        if self.i18n:
            return Paginator(LanguagePairs(entries, self.language_codes()), self.limit)
        return Paginator(entries, self.limit)

    def language_codes(self) -> list[str]:
        """Return the declared `languages`, every code of `LANGUAGES` without them."""
        return sitemap_languages(self.options)

    @override
    def location(self, item: SitemapItem) -> str:
        """Reverse the item under the active language, so its prefix is in the path."""
        kwargs: dict[str, Any] = dict(item.entry.kwargs)
        try:
            return page_reverse(item.trail, **kwargs)
        except NoReverseMatch as exc:
            exc.add_note(
                f"Raised reversing the trail {item.trail!r} with the kwargs "
                f"{sorted(kwargs)} the sitemap of {self.seo_root.path} lists."
            )
            raise

    def lastmod(self, item: SitemapItem) -> datetime.datetime | None:
        """Return the modification time the entry carries, as an aware datetime."""
        stamp = item.entry.lastmod
        return None if stamp is None else lastmod_datetime(stamp)

    def changefreq(self, item: SitemapItem) -> str | None:
        """Return the item value, falling back to the module default."""
        return item.entry.changefreq or self.options.changefreq

    def priority(self, item: SitemapItem) -> float | None:
        """Return the item value, falling back to the module default."""
        own = item.entry.priority
        return self.options.priority if own is None else own

    @override
    def get_domain(self, site: Site | RequestSite | None = None) -> str:
        """Return the domain of `site`, else of the request origin or the site URL."""
        if site is not None:
            return str(site.domain)
        return request_origin(self.request).domain

    @override
    def get_urls(
        self,
        page: int | str = 1,
        site: Site | RequestSite | None = None,
        protocol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return one page of URLs, in the default language unless `i18n` is set.

        The `x-default` alternate is the default language URL, as in the page head.
        """
        if not self.i18n:
            with translation.override(settings.LANGUAGE_CODE):
                return super().get_urls(page, site, protocol)
        urls = super().get_urls(page, site, protocol)
        if self.alternates and self.options.x_default:
            for url in urls:
                alternates = url["alternates"]
                pairs = [(alt["lang_code"], alt["location"]) for alt in alternates]
                fallback = x_default_url(pairs)
                if fallback is not None:
                    alternates.append({"location": fallback, "lang_code": X_DEFAULT})
        return urls

    @override
    def get_latest_lastmod(self) -> datetime.datetime | None:
        """Return the latest `lastmod` of the items, `None` unless every item has one.

        A `QuerySet` part with a `lastmod` field costs one aggregate query.
        """
        latest: datetime.datetime | None = None
        for part in self.entries().parts:
            if part.count() == 0:
                continue
            found = self._part_latest(part)
            if found is None:
                return None
            latest = found if latest is None else max(latest, found)
        return latest

    def _part_latest(self, part: Part[SitemapItem]) -> datetime.datetime | None:
        """Return the latest `lastmod` of one part, `None` when it has no known date."""
        field = part.lastmod_field
        if field is not None and isinstance(part.rows, QuerySet):
            value = part.rows.aggregate(latest=Max(field))["latest"]
            return value if value is None else lastmod_datetime(value)
        if field is None and isinstance(part.rows, QuerySet):
            # Without a named field, the rows of a model carry no lastmod.
            return None
        if part.count() > self.limit:
            return None
        stamps = [item.entry.lastmod for item in part.slice(0, part.count())]
        if None in stamps:
            return None
        return max(lastmod_datetime(stamp) for stamp in stamps if stamp is not None)


__all__ = [
    "MAX_LIMIT",
    "X_DEFAULT",
    "PageTreeSitemap",
    "SitemapItem",
    "SitemapOptions",
    "effective_limit",
    "is_excluded",
    "lastmod_datetime",
    "listed_trails",
    "sitemap_languages",
    "static_noindex",
]
