"""The head markup of a `ResolvedMetadata`, one tag per line in section order.

A renderer sees no request, so every policy is settled before it runs.
"""

import functools
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from itertools import chain
from typing import ClassVar, Final, override

from django.core.serializers.json import DjangoJSONEncoder
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from next.backends import resolve_setting_class
from next.conf.signals import settings_reloaded

from .markers import (
    Article,
    Book,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Profile,
    ResolvedMetadata,
)
from .resolve import CONTEXT


_TITLE: Final = "<title>{}</title>"
_NAMED: Final = '<meta name="{}" content="{}">'
_PROPERTY: Final = '<meta property="{}" content="{}">'
_CANONICAL: Final = '<link rel="canonical" href="{}">'
_ALTERNATE: Final = '<link rel="alternate" hreflang="{}" href="{}">'
_LINK: Final = "<link{}>"
_META: Final = "<meta{}>"
_ATTR: Final = ' {}="{}"'
_JSONLD: Final = '<script type="application/ld+json">{}</script>'
_JSONLD_ESCAPES: Final = {ord("<"): "\\u003C", ord(">"): "\\u003E", ord("&"): "\\u0026"}
_SCHEMA: Final = "https://schema.org"
_IMAGE_DETAILS: Final = ("secure_url", "type", "width", "height", "alt")
_VIDEO_DETAILS: Final = ("secure_url", "type", "width", "height")
_AUDIO_DETAILS: Final = ("secure_url", "type")
_PROFILE: Final = ("first_name", "last_name", "username", "gender")
_ICON_DETAILS: Final = ("type", "sizes", "media", "color")

type Lines = Iterable[SafeString]
type Pairs = Iterable[tuple[str, object]]
type _Media = OpenGraphImage | OpenGraphVideo | OpenGraphAudio
type _MediaGroup = tuple[str, tuple[_Media, ...], tuple[str, ...]]


def _tag(template: str, pairs: Pairs) -> SafeString:
    """Render one void tag of the attribute pairs, a `None` value left out."""
    attrs = ((name, value) for name, value in pairs if value is not None)
    return format_html(template, format_html_join("", _ATTR, attrs))


def _properties(prefix: str, value: object, names: Iterable[str]) -> list[SafeString]:
    """Render the set `names` of a block as `prefix:name` properties."""
    return [
        format_html(_PROPERTY, f"{prefix}:{name}", detail)
        for name in names
        if (detail := getattr(value, name)) is not None
    ]


class MetadataRenderer(ABC):
    """The contract a renderer of one `ResolvedMetadata` fulfils."""

    @abstractmethod
    def render(self, resolved: ResolvedMetadata) -> SafeString:
        """Return the markup of `resolved` for the head of one response."""


class HtmlMetadataRenderer(MetadataRenderer):
    """Render the head tags section by section, every value escaped.

    A subclass reorders or extends `sections` and overrides the `render_*` hooks.
    """

    sections: ClassVar[tuple[str, ...]] = (
        "title",
        "viewport",
        "theme_color",
        "color_scheme",
        "description",
        "keywords",
        "robots",
        "canonical",
        "alternates",
        "feeds",
        "icons",
        "manifest",
        "links",
        "verification",
        "other",
        "og",
        "properties",
        "twitter",
        "jsonld",
    )

    @override
    def render(self, resolved: ResolvedMetadata) -> SafeString:
        """Return the lines of every section joined by newlines, empty for nothing."""
        hooks = (getattr(self, f"render_{name}") for name in self.sections)
        return SafeString("\n".join(chain.from_iterable(h(resolved) for h in hooks)))

    def render_title(self, resolved: ResolvedMetadata) -> Lines:
        """Render the `<title>`."""
        if resolved.title is None:
            return ()
        return (format_html(_TITLE, resolved.title),)

    def render_viewport(self, resolved: ResolvedMetadata) -> Lines:
        """Render the viewport meta."""
        if resolved.viewport is None:
            return ()
        return (format_html(_NAMED, "viewport", resolved.viewport),)

    def render_theme_color(self, resolved: ResolvedMetadata) -> Lines:
        """Render one theme-color meta per color, with its media query."""
        return [
            _tag(
                _META,
                (
                    ("name", "theme-color"),
                    ("content", color.color),
                    ("media", color.media),
                ),
            )
            for color in resolved.theme_color
        ]

    def render_color_scheme(self, resolved: ResolvedMetadata) -> Lines:
        """Render the color-scheme meta."""
        if resolved.color_scheme is None:
            return ()
        return (format_html(_NAMED, "color-scheme", resolved.color_scheme),)

    def render_description(self, resolved: ResolvedMetadata) -> Lines:
        """Render the description meta."""
        if resolved.description is None:
            return ()
        return (format_html(_NAMED, "description", resolved.description),)

    def render_keywords(self, resolved: ResolvedMetadata) -> Lines:
        """Render the keywords as one comma-joined meta."""
        if not resolved.keywords:
            return ()
        joined = ", ".join(str(keyword) for keyword in resolved.keywords)
        return (format_html(_NAMED, "keywords", joined),)

    def render_robots(self, resolved: ResolvedMetadata) -> Lines:
        """Render the robots and googlebot metas."""
        pairs = (("robots", resolved.robots), ("googlebot", resolved.googlebot))
        return [
            format_html(_NAMED, name, value)
            for name, value in pairs
            if value is not None
        ]

    def render_canonical(self, resolved: ResolvedMetadata) -> Lines:
        """Render the canonical link."""
        if resolved.canonical is None:
            return ()
        return (format_html(_CANONICAL, resolved.canonical),)

    def render_alternates(self, resolved: ResolvedMetadata) -> Lines:
        """Render one hreflang link per alternate, x-default last."""
        return [format_html(_ALTERNATE, code, url) for code, url in resolved.alternates]

    def render_feeds(self, resolved: ResolvedMetadata) -> Lines:
        """Render one alternate link per feed."""
        return [
            _tag(
                _LINK,
                (
                    ("rel", "alternate"),
                    ("type", feed.type),
                    ("title", feed.title),
                    ("href", feed.url),
                ),
            )
            for feed in resolved.feeds
        ]

    def render_icons(self, resolved: ResolvedMetadata) -> Lines:
        """Render one link per icon, icon then apple then the other rels."""
        return [
            _tag(
                _LINK,
                (
                    ("rel", icon.rel),
                    ("href", icon.url),
                    *((name, getattr(icon, name)) for name in _ICON_DETAILS),
                ),
            )
            for icon in resolved.icons
        ]

    def render_manifest(self, resolved: ResolvedMetadata) -> Lines:
        """Render the web app manifest link."""
        if resolved.manifest is None:
            return ()
        return (_tag(_LINK, (("rel", "manifest"), ("href", resolved.manifest))),)

    def render_links(self, resolved: ResolvedMetadata) -> Lines:
        """Render the free-form links, their attributes in declared order."""
        return [
            _tag(_LINK, (("rel", link.rel), ("href", link.href), *link.attrs))
            for link in resolved.links
        ]

    def render_verification(self, resolved: ResolvedMetadata) -> Lines:
        """Render one verification meta per token."""
        return [
            format_html(_NAMED, name, token) for name, token in resolved.verification
        ]

    def render_other(self, resolved: ResolvedMetadata) -> Lines:
        """Render the free-form named metas."""
        return [format_html(_NAMED, name, value) for name, value in resolved.other]

    def render_og(self, resolved: ResolvedMetadata) -> Lines:
        """Render the Open Graph properties, the media, and the typed objects."""
        og = resolved.og
        if og is None:
            return ()
        fields = (
            ("og:title", og.title),
            ("og:description", og.description),
            ("og:url", og.url),
            ("og:type", og.type),
            ("og:site_name", og.site_name),
            ("og:locale", og.locale),
        )
        lines = [
            format_html(_PROPERTY, name, value)
            for name, value in fields
            if value is not None
        ]
        alternates = og.locale_alternates
        if not isinstance(alternates, bool):
            lines.extend(
                format_html(_PROPERTY, "og:locale:alternate", code)
                for code in alternates
            )
        if og.determiner is not None:
            lines.append(format_html(_PROPERTY, "og:determiner", og.determiner))
        lines.extend(self._media(og))
        if og.article is not None:
            lines.extend(self._article(og.article))
        if og.profile is not None:
            lines.extend(self._profile(og.profile))
        if og.book is not None:
            lines.extend(self._book(og.book))
        return lines

    def _media(self, og: OpenGraph) -> list[SafeString]:
        groups: tuple[_MediaGroup, ...] = (
            ("og:image", og.images, _IMAGE_DETAILS),
            ("og:video", og.videos, _VIDEO_DETAILS),
            ("og:audio", og.audio, _AUDIO_DETAILS),
        )
        lines: list[SafeString] = []
        for prefix, items, details in groups:
            for item in items:
                lines.append(format_html(_PROPERTY, prefix, item.url))
                lines.extend(_properties(prefix, item, details))
        return lines

    def _article(self, article: Article) -> list[SafeString]:
        lines = _properties("article", article, ("published_time", "modified_time"))
        lines.extend(
            format_html(_PROPERTY, "article:author", author)
            for author in article.authors
        )
        lines.extend(_properties("article", article, ("section",)))
        lines.extend(format_html(_PROPERTY, "article:tag", tag) for tag in article.tags)
        return lines

    def _profile(self, profile: Profile) -> list[SafeString]:
        return _properties("profile", profile, _PROFILE)

    def _book(self, book: Book) -> list[SafeString]:
        lines = [
            format_html(_PROPERTY, "book:author", author) for author in book.authors
        ]
        lines.extend(_properties("book", book, ("isbn", "release_date")))
        lines.extend(format_html(_PROPERTY, "book:tag", tag) for tag in book.tags)
        return lines

    def render_properties(self, resolved: ResolvedMetadata) -> Lines:
        """Render the free-form `property` metas."""
        return [
            format_html(_PROPERTY, name, value) for name, value in resolved.properties
        ]

    def render_twitter(self, resolved: ResolvedMetadata) -> Lines:
        """Render the Twitter card metas, each image with its alt text."""
        twitter = resolved.twitter
        if twitter is None:
            return ()
        fields = (
            ("twitter:card", twitter.card),
            ("twitter:site", twitter.site),
            ("twitter:site:id", twitter.site_id),
            ("twitter:creator", twitter.creator),
            ("twitter:creator:id", twitter.creator_id),
            ("twitter:title", twitter.title),
            ("twitter:description", twitter.description),
        )
        lines = [
            format_html(_NAMED, name, value)
            for name, value in fields
            if value is not None
        ]
        for image in twitter.images:
            lines.append(format_html(_NAMED, "twitter:image", image.url))
            if image.alt is not None:
                lines.append(format_html(_NAMED, "twitter:image:alt", image.alt))
        player = twitter.player
        if player is not None:
            details = (
                ("twitter:player", player.url),
                ("twitter:player:width", player.width),
                ("twitter:player:height", player.height),
                ("twitter:player:stream", player.stream),
            )
            lines.extend(
                format_html(_NAMED, name, value)
                for name, value in details
                if value is not None
            )
        return lines

    def render_jsonld(self, resolved: ResolvedMetadata) -> Lines:
        """Render the graph as one JSON-LD script, a foreign context in its own.

        Every script is closed against a `</script>` in a value.
        """
        graph = [node for node in resolved.jsonld if CONTEXT not in node]
        lines = [self._jsonld({CONTEXT: _SCHEMA, "@graph": graph})] if graph else []
        lines.extend(self._jsonld(node) for node in resolved.jsonld if CONTEXT in node)
        return lines

    def _jsonld(self, obj: Mapping[str, object]) -> SafeString:
        text = json.dumps(obj, cls=DjangoJSONEncoder, allow_nan=False)
        return format_html(_JSONLD, SafeString(text.translate(_JSONLD_ESCAPES)))


def _configured_renderer_class() -> type[MetadataRenderer]:
    """Return the class named by `NEXT_FRAMEWORK["METADATA"]["RENDERER"]`."""
    return resolve_setting_class(
        "RENDERER",
        scope="METADATA",
        base=MetadataRenderer,
        shipped=HtmlMetadataRenderer,
        base_path="next.pages.MetadataRenderer",
    )


@functools.cache
def metadata_renderer() -> MetadataRenderer:
    """Return the renderer `METADATA["RENDERER"]` names, built once per reload."""
    return _configured_renderer_class()()


def forget_metadata_renderer(**kwargs) -> None:
    """Drop the memoised renderer, so a settings reload takes effect."""
    metadata_renderer.cache_clear()


settings_reloaded.connect(forget_metadata_renderer)


__all__ = [
    "HtmlMetadataRenderer",
    "MetadataRenderer",
    "forget_metadata_renderer",
    "metadata_renderer",
]
