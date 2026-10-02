"""The resolve stage that turns a fold into the `ResolvedMetadata` of one response.

Request and settings are read here, so a custom renderer cannot lose a policy.
"""

import locale
import re
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from datetime import date, datetime
from functools import lru_cache, partial
from typing import Final
from urllib.parse import urlencode, urljoin, urlsplit

from django.conf import settings
from django.http import HttpRequest
from django.urls import NoReverseMatch, ResolverMatch, reverse
from django.utils.encoding import escape_uri_path, iri_to_uri
from django.utils.functional import Promise
from django.utils.timezone import get_current_timezone, is_naive, make_aware
from django.utils.translation import get_language, to_locale

from next.conf import next_framework_settings
from next.pages.errors import PageMetadataRequestError
from next.ports import router_access_slot
from next.site import SiteOriginError, site_indexable, site_origin
from next.site.headers import CLOSED_ROBOTS

from .dicts import Text, Url
from .hreflang import hreflang_urls, x_default_url
from .ld import ID, BreadcrumbList, ListItem, Node, Ref, node_type
from .markers import (
    NO_BREADCRUMBS,
    Alternates,
    Article,
    Book,
    Breadcrumb,
    Breadcrumbs,
    Crumb,
    Feed,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    ResolvedMetadata,
    Robots,
    Twitter,
    Verification,
    Viewport,
)
from .normalize import X_DEFAULT
from .scope import metadata_options


RESOLVED_METADATA_ATTR: Final = "_next_resolved_metadata"
"""The request attribute a rendered `{% metadata %}` publishes its resolve under."""

SITE_NOINDEX: Final = CLOSED_ROBOTS
"""The robots content every page of a site closed to search engines carries."""

_PAGE_ONE: Final = ("page", "1")
_ROBOTS_BOOLEANS: Final = ("noarchive", "nosnippet", "noimageindex", "notranslate")
_ROBOTS_LIMITS: Final = (
    ("unavailable_after", "unavailable_after: "),
    ("max_snippet", "max-snippet:"),
    ("max_image_preview", "max-image-preview:"),
    ("max_video_preview", "max-video-preview:"),
)
_VERIFICATION: Final = (
    ("google", "google-site-verification"),
    ("yandex", "yandex-verification"),
    ("bing", "msvalidate.01"),
    ("pinterest", "p:domain_verify"),
    ("facebook", "facebook-domain-verification"),
)
_VIEWPORT: Final = tuple(
    (item.name, item.name.replace("_", "-")) for item in fields(Viewport)
)
FEED_TYPES: Final = {
    "rss": "application/rss+xml",
    "atom": "application/atom+xml",
    "json": "application/feed+json",
}
"""The media type each short feed type names."""

ORIGIN_RELS: Final = frozenset({"preconnect", "dns-prefetch"})
"""The link rels whose `href` is an origin, left as declared."""

OG_LOCALE: Final = re.compile(r"[a-z]{2,3}_[A-Z]{2}")
"""The `ll_CC` form an `og:locale` must take."""

OG_LOCALES: Final[Mapping[str, str]] = {
    "ar": "ar_AR",
    "bn": "bn_IN",
    "hy": "hy_AM",
    "kk": "kk_KZ",
    "mn": "mn_MN",
    "ne": "ne_NP",
    "no": "nb_NO",
    "sr-latn": "sr_RS",
    "sw": "sw_KE",
    "zh-hans": "zh_CN",
    "zh-hant": "zh_TW",
}
"""The languages whose `locale.normalize` form Facebook's locale list does not hold.

Arabic and Norwegian normalise off the list, and the rest derive no region at all.
"""

MISSPELT_OG_LOCALES: Final[Mapping[str, str]] = {"ar_AA": "ar_AR", "no_NO": "nb_NO"}
"""The `ll_CC` spellings Open Graph ignores, each with the one Facebook reads."""

SCHEMA_CONTEXTS: Final = frozenset(
    {
        "https://schema.org",
        "http://schema.org",
        "https://schema.org/",
        "http://schema.org/",
    }
)
"""The `@context` values the single graph stands for."""

CONTEXT: Final = "@context"
_BREADCRUMB_LIST: Final = "BreadcrumbList"
_ENOUGH_CRUMBS: Final = 2
_ROUTE_MEMO: Final = 1024

type UrlMap = Callable[[Url], str]
type IdMap = Callable[[str], str]


def _origin(request: HttpRequest | None, url: str) -> tuple[str, str]:
    """Return the site origin, the error naming `url` when there is none."""
    try:
        return site_origin(request)
    except SiteOriginError as exc:
        raise SiteOriginError(url) from exc


def _plain_url(url: Url) -> str:
    """Return a declared URL as a string, a lazy one forced in the current language."""
    return str(url) if isinstance(url, Promise) else url


def absolute_url(url: str, *, request: HttpRequest | None = None) -> str:
    """Return `url` absolute on the site origin, the one the sitemap lists on.

    A URL with a scheme keeps its origin, and a protocol-relative one takes a scheme,
    each encoded the way a relative one is.
    """
    parts = urlsplit(url)
    if parts.scheme:
        return iri_to_uri(url)
    if parts.netloc:
        return f"{_origin(request, url)[0]}:{iri_to_uri(url)}"
    if not url.startswith("/"):
        if request is None:
            raise SiteOriginError(url)
        url = urljoin(escape_uri_path(request.path), url)
    scheme, host = _origin(request, url)
    return f"{scheme}://{host}{iri_to_uri(url)}"


def self_path(request: HttpRequest) -> str:
    """Return the escaped request path with the allowlisted query, the self URL."""
    query = request.GET
    pairs = [
        (key, value)
        for key in metadata_options().canonical_query
        for value in query.getlist(key)
        if (key, value) != _PAGE_ONE
    ]
    path = escape_uri_path(request.path)
    return f"{path}?{urlencode(pairs)}" if pairs else path


def _page_path(meta: Metadata, request: HttpRequest | None, key: str) -> str:
    """Return the declared canonical, `True` and its absence read as the self path."""
    canonical = meta.canonical
    if canonical is not None and canonical is not True:
        return _plain_url(canonical)
    if request is None:
        raise PageMetadataRequestError(key)
    return self_path(request)


def robots_content(robots: Robots | str) -> str:
    """Fold the directives to the comma-joined content of a robots meta."""
    if isinstance(robots, str):
        return robots
    parts: list[str] = []
    if robots.index is not None:
        parts.append("index" if robots.index else "noindex")
    if robots.follow is not None:
        parts.append("follow" if robots.follow else "nofollow")
    parts.extend(flag for flag in _ROBOTS_BOOLEANS if getattr(robots, flag))
    for name, label in _ROBOTS_LIMITS:
        limit = getattr(robots, name)
        if limit is not None:
            parts.append(f"{label}{limit}")
    return ", ".join(parts)


def robots_contents(
    meta: Metadata, *, indexable: bool
) -> tuple[str | None, str | None]:
    """Return the robots and googlebot contents, the site rule taking precedence."""
    if not indexable:
        return SITE_NOINDEX, None
    robots = meta.robots
    if robots is None:
        return None, None
    googlebot = robots.googlebot if isinstance(robots, Robots) else None
    return (
        robots_content(robots) or None,
        None if googlebot is None else robots_content(googlebot) or None,
    )


def _canonical(meta: Metadata, request: HttpRequest | None) -> str | None:
    if meta.canonical is None:
        return None
    return absolute_url(_page_path(meta, request, "canonical"), request=request)


def _alternates(
    meta: Metadata, request: HttpRequest | None
) -> tuple[tuple[str, str], ...]:
    """Return the hreflang pairs with exactly one x-default, last."""
    alternates = meta.alternates
    if alternates is None:
        return ()
    absolute = _url_map(request)
    languages = alternates.languages
    if isinstance(languages, tuple):
        pairs = tuple(item for item in languages if item[0] != X_DEFAULT)
        fallback = next((url for code, url in languages if code == X_DEFAULT), None)
    elif languages is True:
        pairs = hreflang_urls(_page_path(meta, request, "alternates"))
        fallback = x_default_url(pairs)
    else:
        return ()
    if not pairs:
        return ()
    x_default = alternates.x_default or fallback
    if x_default is not None:
        pairs = (*pairs, (X_DEFAULT, x_default))
    return tuple((code, absolute(url)) for code, url in pairs)


def iso_time(value: date | str) -> str:
    """Return an Open Graph time, a naive datetime read in the current time zone."""
    if isinstance(value, datetime):
        if is_naive(value):
            value = make_aware(value, get_current_timezone())
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _article(article: Article | None) -> Article | None:
    if article is None:
        return None
    published, modified = article.published_time, article.modified_time
    return replace(
        article,
        published_time=None if published is None else iso_time(published),
        modified_time=None if modified is None else iso_time(modified),
    )


def _book(book: Book | None) -> Book | None:
    if book is None or book.release_date is None:
        return book
    return replace(book, release_date=iso_time(book.release_date))


def _first(value: Text | None, fallback: Text | None) -> Text | None:
    return fallback if value is None else value


def og_locale(language: str | None) -> str | None:
    """Return the `ll_CC` Open Graph locale of a language code, `None` without one.

    A code without a region takes the one `locale.normalize` names, `en` as `en_US`,
    unless `OG_LOCALES` holds the one Facebook reads instead.
    """
    if not language:
        return None
    known = OG_LOCALES.get(language.lower())
    if known is not None:
        return known
    name = to_locale(language)
    if "_" not in name:
        name = locale.normalize(name).split(".")[0]
    return name if OG_LOCALE.fullmatch(name) else None


def _locale_alternates(og: OpenGraph, current: str | None) -> tuple[str, ...]:
    """Return the alternates as declared, or every other language of `LANGUAGES`."""
    alternates = og.locale_alternates
    if alternates is False:
        return ()
    if alternates is not True:
        return alternates
    found = (og_locale(code) for code, _name in settings.LANGUAGES)
    return tuple(dict.fromkeys(name for name in found if name and name != current))


def _media_urls[T: (OpenGraphImage, OpenGraphVideo, OpenGraphAudio)](
    items: tuple[T, ...], url: UrlMap
) -> tuple[T, ...]:
    """Return the media with the URL and the secure URL of each absolute."""
    return tuple(
        replace(
            item,
            url=None if item.url is None else url(item.url),
            secure_url=None if item.secure_url is None else url(item.secure_url),
        )
        for item in items
    )


def _open_graph(
    meta: Metadata, canonical: str | None, request: HttpRequest | None
) -> OpenGraph | None:
    """Return the og block with absolute URLs and the page values as fallbacks."""
    og = meta.og
    if og is None:
        return None
    url = _url_map(request)
    current = og.locale or og_locale(get_language())
    return replace(
        og,
        title=_first(og.title, meta.title),
        description=_first(og.description, meta.description),
        url=canonical if og.url is None else url(og.url),
        site_name=_first(og.site_name, meta.site_name),
        locale=current,
        locale_alternates=_locale_alternates(og, current),
        images=_media_urls(og.images, url),
        videos=_media_urls(og.videos, url),
        audio=_media_urls(og.audio, url),
        article=_article(og.article),
        book=_book(og.book),
    )


def _twitter(twitter: Twitter | None, request: HttpRequest | None) -> Twitter | None:
    if twitter is None:
        return None
    url = _url_map(request)
    images = tuple(replace(image, url=url(image.url)) for image in twitter.images)
    player = twitter.player
    if player is not None:
        stream = None if player.stream is None else url(player.stream)
        player = replace(player, url=url(player.url), stream=stream)
    return replace(twitter, images=images, player=player)


def _verification(verification: Verification | None) -> tuple[tuple[str, Text], ...]:
    if verification is None:
        return ()
    engines = tuple(
        (name, token)
        for field, name in _VERIFICATION
        for token in getattr(verification, field)
    )
    return (*engines, *verification.other)


def _url_map(request: HttpRequest | None) -> UrlMap:
    """Return `absolute_url` bound to `request`."""

    def url(value: Url) -> str:
        return absolute_url(_plain_url(value), request=request)

    return url


def _id_map(request: HttpRequest | None) -> IdMap:
    """Return the map of a JSON-LD `@id` or URL, a bare fragment on the site root."""

    def url(value: str) -> str:
        return absolute_url(
            f"/{value}" if value.startswith("#") else value, request=request
        )

    return url


def viewport_content(viewport: Viewport | str) -> str:
    """Return the `content` of the viewport meta, a string taken as written."""
    if isinstance(viewport, str):
        return viewport
    parts: list[str] = []
    for field, name in _VIEWPORT:
        value = getattr(viewport, field)
        if value is None:
            continue
        if isinstance(value, bool):
            value = "yes" if value else "no"
        elif isinstance(value, float):
            value = f"{value:g}"
        parts.append(f"{name}={value}")
    return ", ".join(parts)


def _icons(icons: tuple[Icon, ...], url: UrlMap) -> tuple[Icon, ...]:
    return tuple(replace(icon, url=url(icon.url)) for icon in icons)


def _links(links: tuple[Link, ...], url: UrlMap) -> tuple[Link, ...]:
    """Return the links with absolute hrefs, an origin rel keeping its own."""
    return tuple(
        link
        if ORIGIN_RELS.intersection(link.rel.split())
        else replace(link, href=url(link.href))
        for link in links
    )


def _feeds(alternates: Alternates | None, url: UrlMap) -> tuple[Feed, ...]:
    if alternates is None:
        return ()
    return tuple(
        Feed(url(feed.url), FEED_TYPES.get(feed.type, feed.type), feed.title)
        for feed in alternates.feeds
    )


def _manifest(meta: Metadata, request: HttpRequest | None) -> str | None:
    """Return the declared manifest as an absolute URL."""
    declared = meta.manifest
    return None if declared is None else _url_map(request)(declared)


@lru_cache(maxsize=_ROUTE_MEMO)
def _crumb_route(trail: str, template: str) -> tuple[str, tuple[str, ...]]:
    """Return the URL name and the parameter names of the page at `trail`, read once."""
    parser = router_access_slot.get().url_parser()
    _route, parameters = parser.parse_url_pattern(trail)
    return template.format(name=parser.prepare_url_name(trail)), tuple(parameters)


def _crumb_url(
    trail: str, match: ResolverMatch, template: str, urlconf: str | None
) -> str | None:
    """Reverse the page of `trail` with the URL kwargs of the current match."""
    name, parameters = _crumb_route(trail, template)
    given = match.kwargs
    if any(key not in given for key in parameters):
        return None
    viewname = f"{match.namespace}:{name}" if match.namespace else name
    kwargs = {key: given[key] for key in parameters}
    try:
        return reverse(viewname, urlconf=urlconf, kwargs=kwargs or None)
    except NoReverseMatch:
        return None


def _linked_crumbs(
    crumbs: tuple[Crumb, ...],
    match: ResolverMatch | None,
    path: str,
    urlconf: str | None,
) -> tuple[Breadcrumb, ...]:
    """Reverse the crumbs against `match`, the one at the request `path` current."""
    if match is None:
        return tuple(Breadcrumb(crumb.label, None) for crumb in crumbs)
    template = next_framework_settings.URL_NAME_TEMPLATE
    own = escape_uri_path(path)
    linked: list[Breadcrumb] = []
    for crumb in crumbs:
        url = _crumb_url(crumb.trail, match, template, urlconf)
        linked.append(
            Breadcrumb(crumb.label, url, current=url is not None and url == own)
        )
    return tuple(linked)


def _breadcrumbs(meta: Metadata, request: HttpRequest | None) -> Breadcrumbs:
    """Return the crumbs of the fold, reversed on first read under the request URLconf.

    That read falls inside the render, so the active language is still the request's.
    """
    if not meta.breadcrumbs:
        return NO_BREADCRUMBS
    match = getattr(request, "resolver_match", None)
    if request is None or not isinstance(match, ResolverMatch):
        return Breadcrumbs(partial(_linked_crumbs, meta.breadcrumbs, None, "", None))
    urlconf = getattr(request, "urlconf", None)
    return Breadcrumbs(
        partial(_linked_crumbs, meta.breadcrumbs, match, request.path, urlconf)
    )


def _graph_value(value: object, url: IdMap) -> object:
    """Return one raw JSON-LD value with typed nodes rendered and `@id` resolved."""
    if isinstance(value, Node | Ref):
        return value.as_jsonld(url)
    if isinstance(value, Mapping):
        return {
            key: url(item)
            if key == ID and isinstance(item, str)
            else _graph_value(item, url)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_graph_value(item, url) for item in value]
    return value


def _verbatim(value: str) -> str:
    return value


def _raw_node(item: Mapping[str, object], url: IdMap) -> Mapping[str, object]:
    """Resolve a raw mapping into the graph, one under a foreign context kept whole.

    A typed value inside a foreign context still renders, its `@id` as written.
    """
    context = item.get(CONTEXT)
    if context is not None and not (
        isinstance(context, str) and context in SCHEMA_CONTEXTS
    ):
        kept = _graph_value(item, _verbatim)
        return kept if isinstance(kept, dict) else {}
    graphed = _graph_value({k: v for k, v in item.items() if k != CONTEXT}, url)
    return graphed if isinstance(graphed, dict) else {}


def _crumb_list(
    meta: Metadata, request: HttpRequest, crumbs: Breadcrumbs
) -> BreadcrumbList | None:
    """Return the trail as a `BreadcrumbList`, none if declared or a parent unlinked."""
    if any(node_type(item) == _BREADCRUMB_LIST for item in meta.jsonld):
        return None
    linked = crumbs.items()
    if any(crumb.url is None for crumb in linked[:-1]):
        return None
    own = absolute_url(escape_uri_path(request.path), request=request)
    return BreadcrumbList(
        id=f"{own}#breadcrumb",
        items=tuple(
            ListItem(name=crumb.label, position=position, item=crumb.url)
            for position, crumb in enumerate(linked, start=1)
        ),
    )


def _jsonld(
    meta: Metadata, request: HttpRequest | None, crumbs: Breadcrumbs
) -> tuple[Mapping[str, object], ...]:
    """Return every node of the graph resolved, the breadcrumb list added if none."""
    listed = len(meta.breadcrumbs) >= _ENOUGH_CRUMBS
    if not meta.jsonld and not listed:
        return ()
    url = _id_map(request)
    nodes = [
        item.as_jsonld(url) if isinstance(item, Node) else _raw_node(item, url)
        for item in meta.jsonld
    ]
    trail = (
        None if request is None or not listed else _crumb_list(meta, request, crumbs)
    )
    if trail is not None:
        nodes.append(trail.as_jsonld(url))
    return tuple(nodes)


def resolve_metadata(
    meta: Metadata, *, request: HttpRequest | None
) -> ResolvedMetadata:
    """Resolve a fold for one response, against the request and the site settings.

    The site rule of `SITE["INDEXABLE"]` overrides the robots the chain declared.
    """
    indexable = site_indexable(request)
    robots, googlebot = robots_contents(meta, indexable=indexable)
    canonical = _canonical(meta, request)
    url = _url_map(request)
    crumbs = _breadcrumbs(meta, request)
    return ResolvedMetadata(
        title=meta.title,
        description=meta.description,
        noindex=not indexable or meta.noindex,
        robots=robots,
        googlebot=googlebot,
        canonical=canonical,
        alternates=_alternates(meta, request),
        verification=_verification(meta.verification),
        other=meta.other,
        og=_open_graph(meta, canonical, request),
        twitter=_twitter(meta.twitter, request),
        jsonld=_jsonld(meta, request, crumbs),
        source=meta,
        keywords=meta.keywords,
        viewport=None if meta.viewport is None else viewport_content(meta.viewport),
        theme_color=meta.theme_color,
        color_scheme=meta.color_scheme,
        feeds=_feeds(meta.alternates, url),
        icons=_icons(meta.icons, url),
        manifest=_manifest(meta, request),
        links=_links(meta.links, url),
        properties=meta.properties,
        crumbs=crumbs,
    )


def publish_metadata(request: HttpRequest, resolved: ResolvedMetadata) -> None:
    """Leave the resolve of a render on its request for the response layer."""
    setattr(request, RESOLVED_METADATA_ATTR, resolved)


def published_metadata(request: HttpRequest | None) -> ResolvedMetadata | None:
    """Return the resolve a render published on `request`, if one ran."""
    resolved = getattr(request, RESOLVED_METADATA_ATTR, None)
    return resolved if isinstance(resolved, ResolvedMetadata) else None


__all__ = [
    "CONTEXT",
    "FEED_TYPES",
    "MISSPELT_OG_LOCALES",
    "OG_LOCALE",
    "OG_LOCALES",
    "ORIGIN_RELS",
    "RESOLVED_METADATA_ATTR",
    "SCHEMA_CONTEXTS",
    "SITE_NOINDEX",
    "absolute_url",
    "iso_time",
    "og_locale",
    "publish_metadata",
    "published_metadata",
    "resolve_metadata",
    "robots_content",
    "robots_contents",
    "self_path",
    "viewport_content",
]
