"""The head markup of a folded `Metadata`, one tag per line in a fixed order."""

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Final, override
from urllib.parse import urlencode, urljoin, urlsplit

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.core.serializers.json import DjangoJSONEncoder
from django.core.signals import setting_changed
from django.http import HttpRequest
from django.urls import get_urlconf, translate_url
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import get_language, to_locale

from next.caches import LruCache
from next.conf.signals import settings_reloaded
from next.pages.errors import PageMetadataShapeError, PageMetadataURLError

from .defaults import metadata_options
from .schema import Alternates, Article, Metadata, OpenGraph, Robots, Text, Twitter


_TITLE: Final = "<title>{}</title>"
_NAMED: Final = '<meta name="{}" content="{}">'
_PROPERTY: Final = '<meta property="{}" content="{}">'
_CANONICAL: Final = '<link rel="canonical" href="{}">'
_ALTERNATE: Final = '<link rel="alternate" hreflang="{}" href="{}">'
_JSONLD: Final = '<script type="application/ld+json">{}</script>'
_JSONLD_ESCAPES: Final = {ord("<"): "\\u003C", ord(">"): "\\u003E", ord("&"): "\\u0026"}
_SCHEMES: Final = frozenset({"http", "https"})
_PAGE_ONE: Final = ("page", "1")
_NOINDEX: Final = "noindex, nofollow"
_ROBOTS_FLAGS: Final = ("noarchive", "nosnippet", "noimageindex", "notranslate")
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
)
_URLCONF_SETTINGS: Final = frozenset({"ROOT_URLCONF", "LANGUAGES"})
_CANONICAL_SELF: Final = "canonical"
_ALTERNATES_SELF: Final = "alternates"
_METADATA_SOURCE: Final = "metadata"

_translated: Final[LruCache[tuple[str | None, str, str], str]] = LruCache()


def absolute_url(url: str, *, base: str | None, request: HttpRequest | None) -> str:
    """Return `url` absolute, against `base` first and the request host second."""
    scheme = urlsplit(url).scheme
    if scheme in _SCHEMES:
        return url
    if scheme:
        detail = f"carries the URL {url!r} with a scheme outside http and https"
        raise PageMetadataShapeError(_METADATA_SOURCE, detail)
    if not url.startswith("/"):
        if request is None:
            raise PageMetadataURLError(url)
        url = urljoin(request.path, url)
    if base is not None:
        return base.rstrip("/") + url
    if request is None:
        raise PageMetadataURLError(url)
    return request.build_absolute_uri(url)


def _self_path(request: HttpRequest) -> str:
    """Return the request path with the allowlisted query, the self canonical."""
    query = request.GET
    pairs = [
        (key, value)
        for key in metadata_options().canonical_query
        for value in query.getlist(key)
        if (key, value) != _PAGE_ONE
    ]
    if not pairs:
        return request.path
    return f"{request.path}?{urlencode(pairs)}"


def _canonical_path(meta: Metadata, request: HttpRequest | None) -> str | None:
    """Return the canonical as declared, `True` read as the self path."""
    canonical = meta.canonical
    if canonical is None or canonical is False:
        return None
    if canonical is True:
        if request is None:
            raise PageMetadataURLError(_CANONICAL_SELF)
        return _self_path(request)
    return canonical


def _translated_url(path: str, code: str) -> str:
    """Return `path` under the language `code`, memoised per URLconf."""
    key = (get_urlconf(), path, code)
    url = _translated.get(key)
    if url is None:
        url = translate_url(path, code)
        _translated[key] = url
    return url


def forget_translated_urls(**kwargs) -> None:
    """Drop the hreflang memo, which a URLconf or language change invalidates."""
    _translated.clear()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    if setting in _URLCONF_SETTINGS:
        _translated.clear()


settings_reloaded.connect(forget_translated_urls)
setting_changed.connect(_on_setting_changed)


def _alternate_links(
    alternates: Alternates, meta: Metadata, request: HttpRequest | None
) -> list[tuple[str, str]]:
    """Return the hreflang pairs, from the mapping or the localised URLconf."""
    languages = alternates.languages
    x_default = alternates.x_default
    if isinstance(languages, Mapping):
        links = list(languages.items())
    elif languages is True:
        urlconf = get_urlconf() or str(getattr(settings, "ROOT_URLCONF", ""))
        used, _prefixed = is_language_prefix_patterns_used(urlconf)
        if not used:
            return []
        path = _canonical_path(meta, request)
        if path is None:
            if request is None:
                raise PageMetadataURLError(_ALTERNATES_SELF)
            path = _self_path(request)
        links = [(code, _translated_url(path, code)) for code, _ in settings.LANGUAGES]
        if x_default is None:
            x_default = _translated_url(path, settings.LANGUAGE_CODE)
    else:
        return []
    if x_default is not None:
        links.append(("x-default", x_default))
    return links


def _robots_value(robots: Robots | str) -> str:
    """Fold the directives to the comma-joined content of a robots meta."""
    if isinstance(robots, str):
        return robots
    parts: list[str] = []
    if robots.index is not None:
        parts.append("index" if robots.index else "noindex")
    if robots.follow is not None:
        parts.append("follow" if robots.follow else "nofollow")
    parts.extend(flag for flag in _ROBOTS_FLAGS if getattr(robots, flag))
    for name, label in _ROBOTS_LIMITS:
        limit = getattr(robots, name)
        if limit is not None:
            parts.append(f"{label}{limit}")
    return ", ".join(parts)


def _time(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else value


def _first(value: Text | None, fallback: Text | None) -> Text | None:
    return fallback if value is None else value


class MetadataRenderer(ABC):
    """The contract a renderer of a folded `Metadata` fulfils."""

    @abstractmethod
    def render(self, meta: Metadata, *, request: HttpRequest | None) -> SafeString:
        """Return the markup of `meta` for the head of one response."""


class HtmlMetadataRenderer(MetadataRenderer):
    """Render the head tags, every value escaped and every URL made absolute."""

    @override
    def render(self, meta: Metadata, *, request: HttpRequest | None) -> SafeString:
        """Return the head lines of `meta` joined by newlines, empty for nothing."""
        lines: list[SafeString] = []
        base = meta.base
        if meta.title is not None:
            lines.append(format_html(_TITLE, meta.title))
        if meta.description is not None:
            lines.append(format_html(_NAMED, "description", meta.description))
        lines.extend(self._robots(meta))
        canonical = self._canonical(meta, request)
        if canonical is not None:
            lines.append(format_html(_CANONICAL, canonical))
        lines.extend(self._alternates(meta, request))
        lines.extend(self._verification(meta))
        lines.extend(format_html(_NAMED, name, value) for name, value in meta.other)
        if meta.og is not None:
            lines.extend(self._open_graph(meta, meta.og, canonical, base, request))
        if meta.twitter is not None:
            lines.extend(self._twitter(meta.twitter, base, request))
        lines.extend(self._jsonld(obj) for obj in meta.jsonld)
        return SafeString("\n".join(lines))

    def _robots(self, meta: Metadata) -> list[SafeString]:
        if metadata_options().noindex:
            return [format_html(_NAMED, "robots", _NOINDEX)]
        robots = meta.robots
        if robots is None:
            return []
        lines: list[SafeString] = []
        value = _robots_value(robots)
        if value:
            lines.append(format_html(_NAMED, "robots", value))
        googlebot = robots.googlebot if isinstance(robots, Robots) else None
        if googlebot is not None:
            value = _robots_value(googlebot)
            if value:
                lines.append(format_html(_NAMED, "googlebot", value))
        return lines

    def _canonical(self, meta: Metadata, request: HttpRequest | None) -> str | None:
        path = _canonical_path(meta, request)
        if path is None:
            return None
        return absolute_url(path, base=meta.base, request=request)

    def _alternates(
        self, meta: Metadata, request: HttpRequest | None
    ) -> list[SafeString]:
        if meta.alternates is None:
            return []
        return [
            format_html(
                _ALTERNATE, code, absolute_url(url, base=meta.base, request=request)
            )
            for code, url in _alternate_links(meta.alternates, meta, request)
        ]

    def _verification(self, meta: Metadata) -> list[SafeString]:
        verification = meta.verification
        if verification is None:
            return []
        return [
            format_html(_NAMED, name, token)
            for field, name in _VERIFICATION
            for token in getattr(verification, field)
        ]

    def _open_graph(
        self,
        meta: Metadata,
        og: OpenGraph,
        canonical: str | None,
        base: str | None,
        request: HttpRequest | None,
    ) -> list[SafeString]:
        language = get_language()
        locale = og.locale
        if locale is None and language is not None:
            locale = to_locale(language)
        url = canonical
        if og.url is not None:
            url = absolute_url(og.url, base=base, request=request)
        derived = (
            ("og:title", _first(og.title, meta.title)),
            ("og:description", _first(og.description, meta.description)),
            ("og:url", url),
            ("og:type", og.type),
            ("og:site_name", _first(og.site_name, meta.site_name)),
            ("og:locale", locale),
        )
        lines = [
            format_html(_PROPERTY, name, value)
            for name, value in derived
            if value is not None
        ]
        for image in og.images:
            if image.url is not None:
                url = absolute_url(image.url, base=base, request=request)
                lines.append(format_html(_PROPERTY, "og:image", url))
            for name in ("width", "height", "alt"):
                value = getattr(image, name)
                if value is not None:
                    lines.append(format_html(_PROPERTY, f"og:image:{name}", value))
        if og.article is not None:
            lines.extend(self._article(og.article))
        return lines

    def _article(self, article: Article) -> list[SafeString]:
        lines: list[SafeString] = []
        if article.published_time is not None:
            time = _time(article.published_time)
            lines.append(format_html(_PROPERTY, "article:published_time", time))
        if article.modified_time is not None:
            time = _time(article.modified_time)
            lines.append(format_html(_PROPERTY, "article:modified_time", time))
        lines.extend(
            format_html(_PROPERTY, "article:author", author)
            for author in article.authors
        )
        if article.section is not None:
            lines.append(format_html(_PROPERTY, "article:section", article.section))
        lines.extend(format_html(_PROPERTY, "article:tag", tag) for tag in article.tags)
        return lines

    def _twitter(
        self, twitter: Twitter, base: str | None, request: HttpRequest | None
    ) -> list[SafeString]:
        fields = (
            ("twitter:card", twitter.card),
            ("twitter:site", twitter.site),
            ("twitter:creator", twitter.creator),
            ("twitter:title", twitter.title),
            ("twitter:description", twitter.description),
        )
        lines = [
            format_html(_NAMED, name, value)
            for name, value in fields
            if value is not None
        ]
        lines.extend(
            format_html(
                _NAMED, "twitter:image", absolute_url(url, base=base, request=request)
            )
            for url in twitter.images
        )
        return lines

    def _jsonld(self, obj: Mapping[str, object]) -> SafeString:
        text = json.dumps(obj, cls=DjangoJSONEncoder).translate(_JSONLD_ESCAPES)
        return format_html(_JSONLD, SafeString(text))


default_renderer: Final = HtmlMetadataRenderer()
"""The renderer the `{% metadata %}` tag delegates to."""


def render_metadata(meta: Metadata, *, request: HttpRequest | None) -> SafeString:
    """Render `meta` through the default HTML renderer."""
    return default_renderer.render(meta, request=request)


__all__ = [
    "HtmlMetadataRenderer",
    "MetadataRenderer",
    "absolute_url",
    "default_renderer",
    "forget_translated_urls",
    "render_metadata",
]
