"""The `/sitemap.xml`, `/sitemap-<section>.xml` and `/robots.txt` views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.contrib.sitemaps import Sitemap, views as sitemap_views
from django.contrib.sitemaps.views import x_robots_tag
from django.contrib.sites.shortcuts import get_current_site
from django.http import Http404, HttpResponse
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse
from django.utils.http import http_date
from django.views.decorators.cache import cache_page

from next.pages.metadata import site_segment

from .manager import seo_manager
from .robots import RobotsFile
from .sitemaps import lastmod_datetime


if TYPE_CHECKING:
    import datetime
    from collections.abc import Callable

    from django.http import HttpRequest

    from .sitemaps import RouteSitemap


ROBOTS_CONTENT_TYPE = "text/plain; charset=utf-8"
DEFAULT_NAMESPACE = "next"
HOST_ROOT_NAMESPACE = "next_seo"

type View = Callable[..., HttpResponse]


@dataclass(frozen=True, slots=True)
class IndexItem:
    """One `<sitemap>` of the index, what `sitemap_index.xml` reads."""

    location: str
    last_mod: datetime.datetime | datetime.date | None


def _namespace(request: HttpRequest) -> str:
    """Return the namespace the request reached the SEO routes through."""
    match = request.resolver_match
    return (
        match.namespace if match is not None and match.namespace else DEFAULT_NAMESPACE
    )


def _later(
    current: datetime.datetime | None, new: datetime.datetime | datetime.date
) -> datetime.datetime:
    """Return the later of two lastmod values, read the way the sections read them."""
    new = lastmod_datetime(new)
    return new if current is None else max(current, new)


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


def _needs_index(sitemaps: dict[str, RouteSitemap]) -> bool:
    """Whether the sections or their pages need an index rather than one document."""
    return len(sitemaps) > 1 or any(
        sitemap.paginator.num_pages > 1 for sitemap in sitemaps.values()
    )


@x_robots_tag
def _index(request: HttpRequest, sitemaps: dict[str, RouteSitemap]) -> HttpResponse:
    """Answer the index, absolute on `base` where Django's own reads the request."""
    req_site = get_current_site(request)
    namespace = _namespace(request)
    sites: list[IndexItem] = []
    all_lastmod = True
    latest: datetime.datetime | None = None
    for section, sitemap in sitemaps.items():
        protocol = sitemap.get_protocol(request.scheme)
        domain = sitemap.get_domain(req_site)
        url = reverse(f"{namespace}:sitemap_section", kwargs={"section": section})
        absolute = f"{protocol}://{domain}{url}"
        site_lastmod = sitemap.get_latest_lastmod()
        if site_lastmod is None:
            all_lastmod = False
        elif all_lastmod:
            latest = _later(latest, site_lastmod)
        sites.append(IndexItem(absolute, site_lastmod))
        sites.extend(
            IndexItem(f"{absolute}?p={number}", site_lastmod)
            for number in range(2, sitemap.paginator.num_pages + 1)
        )
    headers = None
    if all_lastmod and latest is not None:
        headers = {"Last-Modified": http_date(latest.timestamp())}
    return TemplateResponse(
        request,
        "sitemap_index.xml",
        {"sitemaps": sites},
        content_type="application/xml",
        headers=headers,
    )


def _sitemap(request: HttpRequest, section: str | None = None) -> HttpResponse:
    """Answer one section, the whole set, or the index when the set needs one."""
    if _is_prefixed_copy(request):
        msg = "The sitemap is served at the host root through next.seo.urls"
        raise Http404(msg)
    sitemaps = seo_manager.sitemaps(request=request)
    if not sitemaps:
        msg = "No sitemap.py declares a sitemap, or NOINDEX keeps it unserved"
        raise Http404(msg)
    if section is None and _needs_index(sitemaps):
        return _index(request, sitemaps)
    maps: dict[str, type[Sitemap[Any]] | Sitemap[Any]] = dict(sitemaps)
    return sitemap_views.sitemap(request, maps, section=section)


def _sitemap_url(request: HttpRequest) -> str | None:
    """Return the absolute `sitemap.xml` for the `Sitemap:` line, when one is served."""
    if not seo_manager.has_sitemap():
        return None
    url = reverse(f"{_namespace(request)}:sitemap")
    base = site_segment().base
    if base is not None:
        return base.rstrip("/") + url
    return request.build_absolute_uri(url)


def robots_view(request: HttpRequest) -> HttpResponse:
    """Serve `/robots.txt`, never cached, so a `robots.txt` edit shows at once."""
    if _is_prefixed_copy(request):
        msg = "robots.txt is served at the host root through next.seo.urls"
        raise Http404(msg)
    source = seo_manager.robots_source()
    if source is None:
        msg = "No robots.py or robots.txt declares robots"
        raise Http404(msg)
    if isinstance(source, RobotsFile):
        content = source.read()
        if content is None:
            msg = f"{source.path} is gone"
            raise Http404(msg)
        return HttpResponse(content, content_type=ROBOTS_CONTENT_TYPE)
    return HttpResponse(
        source.render(_sitemap_url(request)), content_type=ROBOTS_CONTENT_TYPE
    )


class _WrappedSitemap:
    """The sitemap view, under `cache_page` when a `sitemap.py` asks, per version."""

    __slots__ = ("held",)

    def __init__(self) -> None:
        """Start with nothing wrapped."""
        self.held: tuple[int, View] | None = None

    def get(self) -> View:
        """Return the view for the current manager version, wrapping on a miss."""
        version = seo_manager.version
        held = self.held
        if held is not None and held[0] == version:
            return held[1]
        seconds = seo_manager.cache_seconds()
        view: View = _sitemap if seconds is None else cache_page(seconds)(_sitemap)
        self.held = (version, view)
        return view


_wrapped_sitemap = _WrappedSitemap()


def sitemap_view(request: HttpRequest, section: str | None = None) -> HttpResponse:
    """Serve `/sitemap.xml` and `/sitemap-<section>.xml`."""
    return _wrapped_sitemap.get()(request, section=section)


__all__ = ["robots_view", "sitemap_view"]
