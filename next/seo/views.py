"""The sitemap and robots views, answering `GET` and `HEAD` only."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Concatenate, Final, cast

from django.conf import settings
from django.contrib.sitemaps.views import SitemapIndexItem, x_robots_tag
from django.core.paginator import EmptyPage, PageNotAnInteger
from django.http import Http404, HttpResponse, HttpResponseNotFound
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse
from django.utils.http import http_date
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_safe

from next.site import site_indexable
from next.site.config import debug_closed
from next.site.headers import site_robots

from .discovery import BrokenSource
from .manager import seo_manager
from .origin import Origin, request_origin
from .robots import CLOSED_ROBOTS_TXT, DeclaredRobots, TextFile
from .routes import DEFAULT_NAMESPACE, HOST_ROOT_NAMESPACE, SECTION_NAME, SITEMAP_NAME
from .sitemaps import lastmod_datetime


if TYPE_CHECKING:
    import datetime

    from django.contrib.sitemaps import Sitemap
    from django.http import HttpRequest

    from next.pages.responses import CacheControl


logger = logging.getLogger(__name__)

TEXT_CONTENT_TYPE: Final = "text/plain; charset=utf-8"
XML_CONTENT_TYPE: Final = "application/xml"
RETRY_AFTER_SECONDS: Final = 300
_NOT_FOUND: Final = "Not found"

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


def _refuse_copy(request: HttpRequest) -> None:
    if _is_prefixed_copy(request):
        msg = "The SEO routes are served at the host root through next.seo.urls"
        raise Http404(msg)


def _broken(source: BrokenSource) -> Http404:
    """Return the 404 of a source that failed to import, its error logged once."""
    return Http404(f"{source.path} failed to import")


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
    except (EmptyPage, PageNotAnInteger) as exc:
        msg = f"No sitemap page {page!r}"
        raise Http404(msg) from exc
    latest = getattr(section, "latest_lastmod", None)
    return TemplateResponse(
        request,
        getattr(section, "template_name", "sitemap.xml"),
        {"urlset": urls},
        content_type=XML_CONTENT_TYPE,
        headers=_last_modified(latest),
    )


def _sitemap(request: HttpRequest, section: str | None = None) -> HttpResponse:
    """Answer one section, the only one, or the index when the set needs one."""
    _refuse_copy(request)
    broken = seo_manager.broken_sitemap()
    if broken is not None:
        raise _broken(broken)
    sections = seo_manager.sections(request)
    if not sections:
        msg = "No backend lists a sitemap, or the site is closed to search"
        raise Http404(msg)
    origin = request_origin(request)
    if section is not None:
        found = sections.get(section)
        if found is None:
            msg = f"No sitemap section {section!r}"
            raise Http404(msg)
        return _urlset(request, found, origin)
    if _needs_index(sections):
        return _index(request, sections, origin)
    return _urlset(request, next(iter(sections.values())), origin)


def _own_sitemap_url(request: HttpRequest) -> str | None:
    """Return the absolute `sitemap.xml` for the `Sitemap:` line, when one is served."""
    if not seo_manager.serves_sitemap():
        return None
    path = reverse(f"{_namespace(request)}:{SITEMAP_NAME}")
    return request_origin(request).url(path)


def _file_response(source: TextFile) -> HttpResponse:
    """Answer a static text file, 404 once it is gone and 503 while it fails to read.

    A 5xx tells a crawler to back off, which beats a 500 it reads as a full block.
    """
    try:
        content = source.read()
    except OSError:
        logger.exception("%s failed to read, so it answers 503", source.path)
        response = HttpResponse(status=503, content_type=TEXT_CONTENT_TYPE)
        response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
        return response
    if content is None:
        msg = f"{source.path} is gone"
        raise Http404(msg)
    return HttpResponse(content, content_type=TEXT_CONTENT_TYPE)


def _robots(request: HttpRequest) -> HttpResponse:
    """Answer the declared robots, or the fixed open document on a closed site.

    Crawlers get in to read each noindex, and a site only `DEBUG` closes shows its own.
    """
    _refuse_copy(request)
    source = seo_manager.robots_source()
    if source is None:
        msg = "No robots.py or robots.txt declares robots"
        raise Http404(msg)
    if isinstance(source, BrokenSource):
        raise _broken(source)
    if not site_indexable(request) and not debug_closed():
        return HttpResponse(CLOSED_ROBOTS_TXT, content_type=TEXT_CONTENT_TYPE)
    if isinstance(source, TextFile):
        return _file_response(source)
    return HttpResponse(
        source.render(request, _own_sitemap_url(request)),
        content_type=TEXT_CONTENT_TYPE,
    )


class _CachedView:
    """One view under `cache_page` while its source asks, rebuilt per manager version.

    The key prefix carries the source fingerprint, so an edit never serves a stale copy.
    """

    __slots__ = ("control", "held", "view")

    def __init__(self, view: View, control: Callable[[], CacheControl | None]) -> None:
        """Wrap nothing until the first request."""
        self.view = view
        self.control = control
        self.held: tuple[int, View] | None = None

    def get(self) -> View:
        """Return the view for the current manager version, wrapping on a miss."""
        version = seo_manager.version
        held = self.held
        if held is not None and held[0] == version:
            return held[1]
        control = self.control()
        view = self.view
        if control is not None:
            view = _with_cache(view, control)
            seconds = control.seconds
            if seconds and control.stores:
                prefix = f"next-seo-{seo_manager.fingerprint()}"
                view = cache_page(seconds, key_prefix=prefix)(view)
        self.held = (version, view)
        return view


def _with_cache(view: View, control: CacheControl) -> View:
    """Wrap `view` so its answer carries the declared `Cache-Control`.

    Every other outcome raises `Http404` past the wrap, so a 404 is never cached.
    """

    @functools.wraps(view)
    def cached(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        response = view(request, *args, **kwargs)
        control.apply(response)
        return response

    return cached


def _robots_cache() -> CacheControl | None:
    source = seo_manager.robots_source()
    return source.cache if isinstance(source, DeclaredRobots) else None


_cached_sitemap = _CachedView(_sitemap, seo_manager.cache_control)
_cached_robots = _CachedView(_robots, _robots_cache)


def _seo_view[**P](
    view: Callable[Concatenate[HttpRequest, P], HttpResponse],
) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
    """Answer `GET` and `HEAD` only, a missing source as a plain 404, closed or not.

    Only `DEBUG` shows why in the body, since the reason may name a file on disk.
    """

    @functools.wraps(view)
    def answer(
        request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs
    ) -> HttpResponse:
        seo_manager.refresh()
        try:
            return view(request, *args, **kwargs)
        except Http404 as exc:
            body = str(exc) if settings.DEBUG else _NOT_FOUND
            return HttpResponseNotFound(body, content_type=TEXT_CONTENT_TYPE)

    return site_robots(require_safe(answer))


@_seo_view
def sitemap_view(request: HttpRequest, section: str | None = None) -> HttpResponse:
    """Serve `/sitemap.xml` and `/sitemap-<section>.xml`."""
    return _cached_sitemap.get()(request, section=section)


@_seo_view
def robots_view(request: HttpRequest) -> HttpResponse:
    """Serve `/robots.txt`."""
    return _cached_robots.get()(request)


__all__ = ["TEXT_CONTENT_TYPE", "XML_CONTENT_TYPE", "robots_view", "sitemap_view"]
