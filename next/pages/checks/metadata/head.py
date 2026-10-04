"""System checks on the links, icons, names and viewport a metadata head declares."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template.base import TextNode

from next.checks import NEXT, SEO
from next.pages.checks.composed import iter_composed_pages
from next.pages.metadata.resolve import (
    MISSPELT_OG_LOCALES,
    OG_LOCALE,
    ORIGIN_RELS,
    og_locale,
    viewport_content,
)

from .pages import folded_pages, loaded_metadata_pages, static_pages
from .scope import declared_segments
from .templates import TemplateSearch, page_template_path, renders_itself


if TYPE_CHECKING:
    from django.template.base import NodeList

    from next.pages.metadata import OpenGraph, Viewport

    from .scope import DeclaredSegment


DEDICATED_RELS: Final = {
    "canonical": "the canonical key",
    "alternate": "alternates, or alternates.feeds for a feed",
    "icon": "icons",
    "shortcut": "icons",
    "apple-touch-icon": "icons.apple",
    "mask-icon": "icons.other",
    "manifest": "the manifest key",
    "stylesheet": "{% use_style %}",
}
"""The rels a `links` entry leaves to the key or the tag that renders them."""

KNOWN_RELS: Final = frozenset(
    {
        "archives",
        "author",
        "bookmark",
        "code-repository",
        "compression-dictionary",
        "dns-prefetch",
        "edit",
        "expect",
        "external",
        "first",
        "help",
        "hub",
        "last",
        "license",
        "me",
        "modulepreload",
        "next",
        "nofollow",
        "noopener",
        "noreferrer",
        "opener",
        "payment",
        "pingback",
        "preconnect",
        "prefetch",
        "preload",
        "prerender",
        "prev",
        "privacy-policy",
        "search",
        "self",
        "shortlink",
        "sitemap",
        "tag",
        "terms-of-service",
        "up",
        "webmention",
        *DEDICATED_RELS,
    }
)
"""The link rels a check knows, the HTML ones and the common microformats."""

TYPED_NAMES: Final = {
    "description": "description",
    "keywords": "keywords",
    "robots": "robots",
    "googlebot": "robots.googlebot",
    "viewport": "viewport",
    "theme-color": "theme_color",
    "color-scheme": "color_scheme",
    "google-site-verification": "verification.google",
    "yandex-verification": "verification.yandex",
    "msvalidate.01": "verification.bing",
    "p:domain_verify": "verification.pinterest",
    "facebook-domain-verification": "verification.facebook",
}
"""The meta names a typed key renders, which `other` leaves to that key."""

TYPED_NAME_PREFIXES: Final = {"twitter:": "twitter"}
"""The meta name prefixes a typed block renders."""

TYPED_PROPERTY_PREFIXES: Final = {
    "og:": "og",
    "article:": "og.article",
    "profile:": "og.profile",
    "book:": "og.book",
}
"""The property prefixes the og block renders, which `properties` leaves to it."""

_SIZES: Final = re.compile(r"any|\d+[xX]\d+(?: \d+[xX]\d+)*")
_SCALES: Final = ("initial-scale", "minimum-scale", "maximum-scale")
_SCALE_RANGE: Final = (0.1, 10.0)
_ZOOM_FLOOR: Final = 2.0
_NO_ZOOM: Final = frozenset({"no", "0"})
_ORIGIN_PATHS: Final = frozenset({"", "/"})
_LITERAL: Final = r"""<meta\b[^>]*\bname\s*=\s*["']?{}\b"""
_LITERALS: Final = {
    key: re.compile(_LITERAL.format(name), re.IGNORECASE)
    for key, name in (("viewport", "viewport"), ("theme_color", "theme-color"))
}


def _declared(item: DeclaredSegment, detail: str, hint: str, code: str) -> Error:
    return Error(f"{item.source} declares {detail}. {hint}", obj=item.obj, id=code)


def _warned(item: DeclaredSegment, detail: str, hint: str, code: str) -> DjangoWarning:
    return DjangoWarning(
        f"{item.source} declares {detail}. {hint}", obj=item.obj, id=code
    )


def _origin(href: str) -> bool:
    parts = urlsplit(href)
    return (
        bool(parts.netloc)
        and parts.path in _ORIGIN_PATHS
        and not parts.query
        and not parts.fragment
    )


def _link_errors(item: DeclaredSegment) -> list[CheckMessage]:
    """Return `next.E122`, `next.E123` and `next.W105` for the free-form links."""
    messages: list[CheckMessage] = []
    for index, link in enumerate(item.segment.metadata.links):
        where = f"links[{index}]"
        rels = link.rel.lower().split()
        messages.extend(
            _declared(
                item,
                f"{where} with rel {rel!r}",
                f"Declare it through {DEDICATED_RELS[rel]}, which renders it.",
                "next.E122",
            )
            for rel in rels
            if rel in DEDICATED_RELS
        )
        href = link.href
        if (
            not ORIGIN_RELS.isdisjoint(rels)
            and isinstance(href, str)
            and not _origin(href)
        ):
            messages.append(
                _declared(
                    item,
                    f"{where} with the href {link.href!r}",
                    "A preconnect or dns-prefetch href is an origin, write it "
                    "without a path.",
                    "next.E123",
                )
            )
        if "preload" in rels and "as" not in dict(link.attrs):
            messages.append(
                _declared(
                    item,
                    f"{where} as a preload without 'as'",
                    "Name what it preloads, the browser ignores it otherwise.",
                    "next.E123",
                )
            )
        messages.extend(
            _warned(
                item,
                f"{where} with the unknown rel {rel!r}",
                "Check the spelling, a browser ignores a rel it does not know.",
                "next.W105",
            )
            for rel in rels
            if rel not in KNOWN_RELS
        )
    return messages


def _icon_errors(item: DeclaredSegment) -> list[CheckMessage]:
    """Return `next.E124` for a malformed icon and `next.W106` for a repeated one."""
    messages: list[CheckMessage] = []
    icons = item.segment.metadata.icons
    for index, icon in enumerate(icons):
        where = f"icons[{index}]"
        if icon.sizes is not None and not _SIZES.fullmatch(icon.sizes):
            messages.append(
                _declared(
                    item,
                    f"{where} with the sizes {icon.sizes!r}",
                    "Write 'any' or sizes of the form 32x32.",
                    "next.E124",
                )
            )
        if icon.type is not None and not icon.type.startswith("image/"):
            messages.append(
                _declared(
                    item,
                    f"{where} with the type {icon.type!r}",
                    "An icon type is an image/* media type.",
                    "next.E124",
                )
            )
        if icon.rel == "mask-icon" and icon.color is None:
            messages.append(
                _declared(
                    item,
                    f"{where} as a mask-icon without a color",
                    "Safari paints a mask icon in its color, add one.",
                    "next.E124",
                )
            )
    repeated = Counter((icon.rel, icon.sizes, icon.media) for icon in icons)
    messages.extend(
        _warned(
            item,
            f"the icon rel {rel!r} with sizes {sizes!r} and media {media!r} twice",
            "Keep one, the browser picks either at random.",
            "next.W106",
        )
        for (rel, sizes, media), count in repeated.items()
        if count > 1
    )
    return messages


def _typed_key(name: str, *, property_: bool) -> str | None:
    """Return the typed key that renders the meta `name`, if one does."""
    lowered = name.lower()
    if not property_ and lowered in TYPED_NAMES:
        return TYPED_NAMES[lowered]
    prefixes = TYPED_PROPERTY_PREFIXES if property_ else TYPED_NAME_PREFIXES
    return next(
        (key for prefix, key in prefixes.items() if lowered.startswith(prefix)), None
    )


def _name_errors(item: DeclaredSegment) -> list[CheckMessage]:
    """Return `next.E125` for a free-form name a typed key renders."""
    meta = item.segment.metadata
    groups = (("other", meta.other, False), ("properties", meta.properties, True))
    names = {
        (group, name, property_)
        for group, pairs, property_ in groups
        for name, _value in pairs
    }
    return [
        _declared(
            item,
            f"{group}[{name!r}]",
            f"Declare it through {key}, which renders it and keeps it typed.",
            "next.E125",
        )
        for group, name, property_ in sorted(names)
        if (key := _typed_key(name, property_=property_)) is not None
    ]


def viewport_pairs(viewport: Viewport | str | None) -> dict[str, str]:
    """Return the lower-cased directives of a viewport, the string form parsed."""
    if viewport is None:
        return {}
    content = viewport_content(viewport)
    pairs: dict[str, str] = {}
    for part in re.split(r"[,;]", content):
        key, sep, value = part.partition("=")
        if sep:
            pairs[key.strip().lower()] = value.strip().lower()
    return pairs


def _scale(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _viewport_errors(item: DeclaredSegment) -> list[CheckMessage]:
    """Return `next.E126` for a scale out of range and `next.W107` for no zoom."""
    pairs = viewport_pairs(item.segment.metadata.viewport)
    low, high = _SCALE_RANGE
    messages: list[CheckMessage] = []
    for key in _SCALES:
        if key not in pairs:
            continue
        scale = _scale(pairs[key])
        if scale is None or not low <= scale <= high:
            messages.append(
                _declared(
                    item,
                    f"the viewport {key} {pairs[key]!r}",
                    "Write a number from 0.1 to 10.",
                    "next.E126",
                )
            )
    maximum = _scale(pairs.get("maximum-scale", ""))
    if pairs.get("user-scalable") in _NO_ZOOM or (
        maximum is not None and maximum < _ZOOM_FLOOR
    ):
        messages.append(
            _warned(
                item,
                "a viewport that keeps the page from zooming",
                "Drop user_scalable=False and keep maximum_scale at 2 or more, "
                "readers with low vision rely on zoom.",
                "next.W107",
            )
        )
    return messages


@register(Tags.templates, NEXT, SEO)
def check_metadata_head_tags(*args, **kwargs) -> list[CheckMessage]:
    """Validate links, icons, names and viewport of every declared segment.

    The ids are `next.E122` to `next.E126` and `next.W105` to `next.W107`.
    """
    pages = loaded_metadata_pages()
    messages: list[CheckMessage] = []
    for item in declared_segments(pages):
        messages.extend(_link_errors(item))
        messages.extend(_icon_errors(item))
        messages.extend(_name_errors(item))
        messages.extend(_viewport_errors(item))
    return messages


def _explicit_locales(og: OpenGraph) -> list[tuple[str, str]]:
    """Return the declared og locales Open Graph cannot read, each with the fix."""
    declared = [] if og.locale is None else [og.locale]
    if not isinstance(og.locale_alternates, bool):
        declared.extend(og.locale_alternates)
    found: list[tuple[str, str]] = []
    for code in declared:
        if not OG_LOCALE.fullmatch(code):
            found.append((code, "Write it like en_US."))
        elif code in MISSPELT_OG_LOCALES:
            found.append((code, f"Facebook reads {MISSPELT_OG_LOCALES[code]}."))
    return found


def _underived_languages(og: OpenGraph) -> list[str]:
    """Return the language codes no `ll_CC` locale is derived from for this block."""
    codes = [settings.LANGUAGE_CODE] if og.locale is None else []
    if og.locale_alternates is True:
        codes.extend(code for code, _name in settings.LANGUAGES)
    return sorted({code for code in codes if og_locale(code) is None})


@register(Tags.templates, NEXT, SEO)
def check_metadata_social_folds(*args, **kwargs) -> list[CheckMessage]:
    """Warn about an og locale Open Graph cannot read (`next.W108`).

    A language with no `ll_CC` form renders no `og:locale`, reported once.
    """
    pages = loaded_metadata_pages()
    messages: list[CheckMessage] = []
    underived: set[str] = set()
    for entry, meta in folded_pages(pages):
        og = meta.og
        if og is None:
            continue
        underived.update(_underived_languages(og))
        messages.extend(
            DjangoWarning(
                f"{entry.page_path} folds the og locale {code!r}, which Open Graph "
                f"does not read. {fix}",
                obj=str(entry.page_path),
                id="next.W108",
            )
            for code, fix in _explicit_locales(og)
        )
    if underived:
        messages.append(
            DjangoWarning(
                f"The languages {', '.join(map(repr, sorted(underived)))} derive no "
                "ll_CC og:locale, so their pages render none. Set og.locale on "
                "those pages, or list og.locale_alternates explicitly.",
                obj=settings,
                id="next.W108",
            )
        )
    return messages


def _literal_test(key: str) -> TemplateSearch:
    pattern = _LITERALS[key]

    def test(nodelist: NodeList) -> bool:
        texts = cast("list[TextNode]", nodelist.get_nodes_by_type(TextNode))
        return any(pattern.search(node.s) for node in texts)

    return TemplateSearch(test)


@register(Tags.templates, NEXT, SEO)
def check_metadata_head_literals(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a template writes the viewport or theme-color a fold declares.

    The id is `next.W109`, since the head would carry two tags that disagree.
    """
    pages = loaded_metadata_pages()
    warnings: list[CheckMessage] = []
    declared = {
        entry.page_path: [key for key in _LITERALS if getattr(meta, key)]
        for entry, meta in static_pages(pages)
    }
    searches = {key: _literal_test(key) for key in _LITERALS}
    for page_path, template in iter_composed_pages():
        keys = declared.get(page_path)
        if not keys or renders_itself(page_path):
            continue
        template_path = page_template_path(page_path)
        for key in keys:
            if not searches[key].reaches(template.nodelist, template_path):
                continue
            name = key.replace("_", "-")
            warnings.append(
                DjangoWarning(
                    f"{page_path} folds the metadata key {key!r}, and its composed "
                    f'template already writes a literal <meta name="{name}">. Drop '
                    "the literal tag, {% metadata %} renders it.",
                    obj=str(page_path),
                    id="next.W109",
                )
            )
    return warnings


__all__ = [
    "DEDICATED_RELS",
    "KNOWN_RELS",
    "TYPED_NAMES",
    "TYPED_NAME_PREFIXES",
    "TYPED_PROPERTY_PREFIXES",
    "check_metadata_head_literals",
    "check_metadata_head_tags",
    "check_metadata_social_folds",
    "viewport_pairs",
]
