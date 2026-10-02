"""The sitemap and robots views, answering `GET` and `HEAD` only."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Concatenate, Final, cast

from django.conf import settings
from django.contrib.sitemaps.views import SitemapIndexItem, x_robots_tag
from django.core.paginator import EmptyPage, PageNotAnInteger
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotFound
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse
from django.utils.http import http_date
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_safe

from next.diagnostics import INTENDED_EXCEPTIONS, FailureLog
from next.site import site_indexable
from next.site.config import site_closed_to_crawlers, site_url_failed
from next.site.headers import site_robots

from .discovery import BrokenSource
from .manager import seo_manager
from .origin import Origin, request_origin
from .robots import CLOSED_ROBOTS_TXT, DeclaredRobots, TextFile
from .routes import (
    DEFAULT_NAMESPACE,
    HOST_ROOT_NAMESPACE,
    ROBOTS_ROUTE,
    SECTION_NAME,
    SITEMAP_NAME,
    SITEMAP_ROUTE,
)
from .sitemaps import lastmod_datetime


if TYPE_CHECKING:
    import datetime

    from django.contrib.sitemaps import Sitemap

    from next.pages.responses import CacheControl


logger = logging.getLogger(__name__)
_failures = FailureLog(logger)

TEXT_CONTENT_TYPE: Final = "text/plain; charset=utf-8"
XML_CONTENT_TYPE: Final = "application/xml"
RETRY_AFTER_SECONDS: Final = 300

# Every 404 body is one of these constants, so no exception text reaches a response.
_NOT_FOUND: Final = "Not found"
_PREFIXED_COPY: Final = (
    "The SEO routes are served at the host root through next.seo.urls"
)
_BROKEN_SITEMAP: Final = "A sitemap.py failed to import, so no partial sitemap ships"
_NO_SITEMAP: Final = "No backend lists a sitemap, or the site is closed to search"
_NO_SECTION: Final = "No sitemap section by that name"
_NO_PAGE: Final = "No sitemap page by that number"
_NO_ROBOTS: Final = "No robots.py or robots.txt declares robots"
_ROBOTS_GONE: Final = "The robots.txt is gone"

type View = Callable[..., HttpResponse]


def _namespace(request: HttpRequest) -> str:
    """Return the namespace the request reached the SEO routes through."""
    match = request.resolver_match
    return (
        match.namespace if match is not None and match.namespace else DEFAULT_NAMESPACE
    )


def _is_prefixed_copy(request: HttpRequest) -> bool:
    """Whether `next.seo.urls` serves this route elsewhere, so this copy answers 404."""
    match = request.resolver_match
    if match is None or match.namespace == HOST_ROOT_NAMESPACE:
        return False
    try:
        address = reverse(
            f"{HOST_ROOT_NAMESPACE}:{match.url_name}", kwargs=match.kwargs
        )
    except NoReverseMatch:
        return False
    return address != request.path


def _not_found(reason: str) -> HttpResponse:
    """Answer a plain 404, its body naming `reason` only under `DEBUG`.

    `reason` is one of the constants above, never the text of an exception.
    """
    body = reason if settings.DEBUG else _NOT_FOUND
    return HttpResponseNotFound(body, content_type=TEXT_CONTENT_TYPE)


def _unavailable() -> HttpResponse:
    """Answer 503 with `Retry-After`, which tells a crawler to come back later.

    A crawler backs off a 5xx and keeps what it read before, where it reads a 4xx on
    `/robots.txt` as no restriction at all under RFC 9309.
    """
    response = HttpResponse(status=503, content_type=TEXT_CONTENT_TYPE)
    response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
    return response


def _later(current: datetime.datetime | None, new: datetime.date) -> datetime.datetime:
    """Return the later of two lastmod values, read the way the sections read them."""
    stamp = lastmod_datetime(new)
    return stamp if current is None else max(current, stamp)


def _needs_index(sections: Mapping[str, Sitemap[Any]]) -> bool:
    """Whether the sections or their pages need an index rather than one document."""
    return len(sections) > 1 or any(
        section.paginator.num_pages > 1 for section in sections.values()
    )


def _last_modified(latest: datetime.datetime | None) -> dict[str, str] | None:
    return None if latest is None else {"Last-Modified": http_date(latest.timestamp())}


@x_robots_tag
def _index(
    request: HttpRequest, sections: Mapping[str, Sitemap[Any]], origin: Origin
) -> HttpResponse:
    """Answer the index, linking every section and every page past its first."""
    namespace = _namespace(request)
    items: list[SitemapIndexItem] = []
    every_dated = True
    latest: datetime.datetime | None = None
    for name, section in sections.items():
        path = reverse(f"{namespace}:{SECTION_NAME}", kwargs={"section": name})
        scheme = section.get_protocol(origin.scheme)
        absolute = f"{scheme}://{origin.domain}{path}"
        section_lastmod = section.get_latest_lastmod()
        if section_lastmod is None:
            every_dated = False
        elif every_dated:
            latest = _later(latest, section_lastmod)
        # django-stubs types `last_mod` as a bool, the template reads a date.
        stamp = cast("bool | None", section_lastmod)
        items.append(SitemapIndexItem(absolute, stamp))
        items.extend(
            SitemapIndexItem(f"{absolute}?p={number}", stamp)
            for number in range(2, section.paginator.num_pages + 1)
        )
    return TemplateResponse(
        request,
        "sitemap_index.xml",
        {"sitemaps": items},
        content_type=XML_CONTENT_TYPE,
        headers=_last_modified(latest if every_dated else None),
    )


@x_robots_tag
def _urlset(
    request: HttpRequest, section: Sitemap[Any], origin: Origin
) -> HttpResponse:
    """Answer one page of one section, through the template the section names."""
    page = request.GET.get("p", 1)
    try:
        urls = section.get_urls(page=page, site=origin.site, protocol=origin.scheme)
    except (EmptyPage, PageNotAnInteger):
        return _not_found(_NO_PAGE)
    latest = getattr(section, "latest_lastmod", None)
    return TemplateResponse(
        request,
        getattr(section, "template_name", "sitemap.xml"),
        {"urlset": urls},
        content_type=XML_CONTENT_TYPE,
        headers=_last_modified(latest),
    )


def _sitemap_refusal(request: HttpRequest) -> HttpResponse | None:
    """Return what answers in place of the sitemap, `None` while it can be built.

    A broken `sitemap.py` answers 404, so no sitemap missing its URLs ever ships.
    """
    if _is_prefixed_copy(request):
        return _not_found(_PREFIXED_COPY)
    if seo_manager.broken_sitemap() is not None:
        return _not_found(_BROKEN_SITEMAP)
    return _unavailable() if site_url_failed(request) else None


def _sitemap(request: HttpRequest, section: str | None = None) -> HttpResponse:
    """Answer one section, the only one, or the index when the set needs one."""
    refused = _sitemap_refusal(request)
    if refused is not None:
        return refused
    sections = seo_manager.sections(request)
    if not sections:
        return _not_found(_NO_SITEMAP)
    origin = request_origin(request)
    if section is None:
        if _needs_index(sections):
            return _index(request, sections, origin)
        return _urlset(request, next(iter(sections.values())), origin)
    found = sections.get(section)
    return _not_found(_NO_SECTION) if found is None else _urlset(request, found, origin)


def _own_sitemap_url(request: HttpRequest) -> str | None:
    """Return the absolute `sitemap.xml` for the `Sitemap:` line, when one is served."""
    if not seo_manager.serves_sitemap():
        return None
    path = reverse(f"{_namespace(request)}:{SITEMAP_NAME}")
    return request_origin(request).url(path)


def _file_response(source: TextFile) -> HttpResponse:
    """Answer a static text file, 404 once it is gone and 503 while it fails to read."""
    try:
        content = source.read()
    except OSError:
        logger.exception("%s failed to read, so it answers 503", source.path)
        return _unavailable()
    if content is None:
        return _not_found(_ROBOTS_GONE)
    return HttpResponse(content, content_type=TEXT_CONTENT_TYPE)


def _robots(request: HttpRequest) -> HttpResponse:
    """Answer the declared robots, or the fixed open document on a closed site.

    Crawlers get in to read each noindex, and a site only `DEBUG` closes shows its own.
    A `robots.py` that failed to import answers 503, since a 404 reads as allow all.
    """
    if _is_prefixed_copy(request):
        return _not_found(_PREFIXED_COPY)
    source = seo_manager.robots_source()
    if source is None:
        return _not_found(_NO_ROBOTS)
    if isinstance(source, BrokenSource):
        return _unavailable()
    if site_closed_to_crawlers(request):
        return HttpResponse(CLOSED_ROBOTS_TXT, content_type=TEXT_CONTENT_TYPE)
    return _source_response(request, source)


def _source_response(
    request: HttpRequest, source: DeclaredRobots | TextFile
) -> HttpResponse:
    """Answer what a robots source serves, 503 while the site URL fails to answer."""
    if isinstance(source, TextFile):
        return _file_response(source)
    if site_url_failed(request):
        return _unavailable()
    return HttpResponse(
        source.render(request, _own_sitemap_url(request)),
        content_type=TEXT_CONTENT_TYPE,
    )


class _CachedView:
    """One view under `cache_page` while its source asks, rebuilt per manager version.

    The key prefix carries the source fingerprint, so an edit never serves a stale
    copy, and the indexability of the request, so a closed host never reads an open
    one.
    """

    __slots__ = ("control", "held", "view")

    def __init__(self, view: View, control: Callable[[], CacheControl | None]) -> None:
        """Wrap nothing until the first request."""
        self.view = view
        self.control = control
        self.held: tuple[int, dict[int, View]] = (-1, {})

    def get(self, request: HttpRequest) -> View:
        """Return the view for the manager version and the indexability of `request`."""
        version = seo_manager.version
        if self.held[0] != version:
            self.held = (version, {})
        indexable = int(site_indexable(request))
        views = self.held[1]
        view = views.get(indexable)
        if view is None:
            view = views[indexable] = self._wrap(indexable)
        return view

    def _wrap(self, indexable: int) -> View:
        control = self.control()
        view = self.view
        if control is not None:
            view = _with_cache(view, control)
            seconds = control.seconds
            if seconds and control.stores:
                prefix = f"next-seo-{seo_manager.fingerprint()}-{indexable}"
                view = cache_page(seconds, key_prefix=prefix)(view)
        return view


def _with_cache(view: View, control: CacheControl) -> View:
    """Wrap `view` so a 200 it answers carries the declared `Cache-Control`.

    A 404 or a 503 carries none, so no shared cache keeps a failure for its age.
    """

    @functools.wraps(view)
    def cached(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        response = view(request, *args, **kwargs)
        if response.status_code == HTTPStatus.OK:
            control.apply(response)
        return response

    return cached


def _robots_cache() -> CacheControl | None:
    source = seo_manager.robots_source()
    return source.cache if isinstance(source, DeclaredRobots) else None


_cached_sitemap = _CachedView(_sitemap, seo_manager.cache_control)
_cached_robots = _CachedView(_robots, _robots_cache)


def _culprit(exc: BaseException) -> str:
    """Return the source the notes on `exc` name, its type when it carries none."""
    notes = getattr(exc, "__notes__", None)
    return " ".join(notes) if notes else type(exc).__name__


type _Route[**P] = Callable[Concatenate[HttpRequest, P], HttpResponse]


def _seo_view[**P](route: str) -> Callable[[_Route[P]], _Route[P]]:
    """Answer `GET` and `HEAD` only, a miss as a plain 404 and a failure as a 503.

    A route runs project code, a backend, an items callable, or a `rules` callable,
    and a 503 tells a crawler to retry where a 500 reads as a failing site. Only
    `DEBUG` raises the failure instead, a note naming its source.
    """

    def decorate(view: _Route[P]) -> _Route[P]:
        @functools.wraps(view)
        def answer(
            request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs
        ) -> HttpResponse:
            try:
                seo_manager.refresh()
                return view(request, *args, **kwargs)
            except INTENDED_EXCEPTIONS as exc:
                if isinstance(exc, Http404):
                    return _not_found(_NOT_FOUND)
                raise
            except Exception as exc:  # noqa: BLE001 - the route runs project code
                culprit = _culprit(exc)
                _failures.contain(
                    exc,
                    (route, type(exc), culprit),
                    "/%s raised %s while it was built, so it answers 503 with "
                    "Retry-After until the cause is fixed. %s",
                    route,
                    type(exc).__name__,
                    culprit,
                )
                return _unavailable()

        return site_robots(require_safe(answer))

    return decorate


@_seo_view(SITEMAP_ROUTE)
def sitemap_view(request: HttpRequest, section: str | None = None) -> HttpResponse:
    """Serve `/sitemap.xml` and `/sitemap-<section>.xml`."""
    return _cached_sitemap.get(request)(request, section=section)


@_seo_view(ROBOTS_ROUTE)
def robots_view(request: HttpRequest) -> HttpResponse:
    """Serve `/robots.txt`."""
    return _cached_robots.get(request)(request)


__all__ = ["TEXT_CONTENT_TYPE", "XML_CONTENT_TYPE", "robots_view", "sitemap_view"]
