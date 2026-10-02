"""System checks for the metadata each routed `page.py` declares and folds to."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT, SEO
from next.checks.common import RegistrationSubject, registration_file_errors
from next.deps.markers import unwrap_annotated
from next.introspect import callable_name
from next.pages.checks.contexts import annotation_mismatch, load_routed_pages
from next.pages.manager import page
from next.pages.metadata import Metadata, noindexed
from next.site import site_url
from next.utils import WEB_SCHEMES

from .links import foreign_origin, url_fields
from .pages import MetadataPage, folded_pages, loaded_metadata_pages
from .scope import declared_segments, segment_errors


if TYPE_CHECKING:
    from .scope import DeclaredSegment


_TWITTER_CARDS: Final = frozenset({"summary", "summary_large_image", "app", "player"})
_ENUMS: Final = (
    ("og.determiner", frozenset({"a", "an", "the", "", "auto"})),
    ("viewport.viewport_fit", frozenset({"auto", "contain", "cover"})),
    (
        "viewport.interactive_widget",
        frozenset({"resizes-visual", "resizes-content", "overlays-content"}),
    ),
)
_COLOR_SCHEMES: Final = frozenset({"normal", "light", "dark", "only"})

_METADATA_SUBJECT = RegistrationSubject(
    decorator="@page.metadata",
    anchor_name="page.py",
    render="page render",
    code="next.E106",
)


def _value_at(meta: Metadata, path: str) -> object:
    """Return the value a dotted key path names, `None` below an unset block."""
    value: object = meta
    for name in path.split("."):
        value = getattr(value, name, None)
    return value


def _page_shape_errors(entry: MetadataPage) -> list[CheckMessage]:
    """Return `next.E102` to `next.E105` and the segment errors of one page."""
    errors: list[CheckMessage] = []
    page_path = entry.page_path
    obj = str(page_path)
    named = entry.entry is not None and entry.raw is entry.entry.func
    if named:
        errors.append(
            Error(
                f"{page_path} names its @page.metadata callable metadata, the name "
                "the metadata dict takes. Rename the callable.",
                obj=obj,
                id="next.E102",
            )
        )
    elif entry.raw is not None and entry.entry is not None:
        errors.append(
            Error(
                f"{page_path} declares both a metadata dict and an @page.metadata "
                "callable. Keep one, the callable when the values depend on the "
                "request and the dict otherwise.",
                obj=obj,
                id="next.E102",
            )
        )
    if entry.raw is not None and not named and not isinstance(entry.raw, Mapping):
        errors.append(
            Error(
                f"{page_path} declares metadata as {type(entry.raw).__name__!r}, "
                "expected a mapping. Write metadata = {...} with the metadata keys.",
                obj=obj,
                id="next.E103",
            )
        )
    if entry.shape_error is not None:
        errors.append(
            Error(
                f"{entry.shape_error}. Fix the key or the value so the page renders "
                "its metadata.",
                obj=obj,
                id="next.E104",
            )
        )
    segment = entry.segment
    if segment is None:
        return errors
    card = _value_at(segment.metadata, "twitter.card")
    if card is not None and card not in _TWITTER_CARDS:
        allowed = ", ".join(sorted(_TWITTER_CARDS))
        errors.append(
            Error(
                f"{page_path} declares twitter.card {card!r}, expected one of "
                f"{allowed}.",
                obj=obj,
                id="next.E104",
            )
        )
    errors.extend(segment_errors(segment, source=str(page_path), obj=obj))
    return errors


@register(Tags.templates, NEXT, SEO)
def check_page_metadata_shape(*args, **kwargs) -> list[CheckMessage]:
    """Validate the metadata dict of each routed page (`next.E102` to `next.E105`)."""
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry in pages:
        errors.extend(_page_shape_errors(entry))
    return errors


@register(Tags.templates, NEXT, SEO)
def check_metadata_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@page.metadata` no page render collects (`next.E106`)."""
    init_errors, _loaded = load_routed_pages()
    if init_errors:
        return init_errors
    registrations = page.metadata_registrations()
    return registration_file_errors(
        _METADATA_SUBJECT,
        registrations=registrations.names,
        misattributed=registrations.misattributed,
    )


@register(Tags.templates, NEXT, SEO)
def check_metadata_callable_returns_mapping(*args, **kwargs) -> list[CheckMessage]:
    """Require a `@page.metadata` callable to be annotated dict-like (`next.E108`)."""
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry in pages:
        if entry.entry is None:
            continue
        func = entry.entry.func
        annotation_name = annotation_mismatch(func)
        if annotation_name is None:
            continue
        errors.append(
            Error(
                f"Metadata callable {callable_name(func)} in {entry.page_path} must "
                "return a mapping of metadata keys (got return annotation "
                f"{annotation_name}). Annotate it '-> dict' or '-> MetadataDict'.",
                obj=str(entry.page_path),
                id="next.E108",
            )
        )
    return errors


def _parent_parameters(func: Callable[..., Any]) -> list[str]:
    """Return the parameters of `func` annotated `Metadata`, which nothing fills."""
    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return []
    return [
        param.name
        for param in parameters
        if unwrap_annotated(param.annotation) is Metadata
        or param.annotation == "Metadata"
    ]


@register(Tags.templates, NEXT, SEO)
def check_metadata_parent_parameter(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@page.metadata` parameter annotated `Metadata` (`next.E121`).

    The fold of the ancestors is no longer injected, the chain merges it instead.
    """
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry in pages:
        if entry.entry is None:
            continue
        func = entry.entry.func
        errors.extend(
            Error(
                f"Metadata callable {callable_name(func)} in {entry.page_path} "
                f"annotates its parameter {name!r} as Metadata, which nothing "
                "fills. Drop it and return only the keys this page sets, the "
                "chain merges them over the ancestors.",
                obj=str(entry.page_path),
                id="next.E121",
            )
            for name in _parent_parameters(func)
        )
    return errors


def _enum_errors(item: DeclaredSegment) -> list[CheckMessage]:
    """Return `next.E104` for each value outside the set its key allows."""
    meta = item.segment.metadata
    errors: list[CheckMessage] = [
        Error(
            f"{item.source} declares {key} {value!r}, expected one of "
            f"{', '.join(repr(choice) for choice in sorted(allowed))}.",
            obj=item.obj,
            id="next.E104",
        )
        for key, allowed in _ENUMS
        if (value := _value_at(meta, key)) is not None and value not in allowed
    ]
    scheme = meta.color_scheme
    if scheme is not None and not set(scheme.split()) <= _COLOR_SCHEMES:
        errors.append(
            Error(
                f"{item.source} declares color_scheme {scheme!r}, expected tokens "
                "of normal, light, dark and only.",
                obj=item.obj,
                id="next.E104",
            )
        )
    return errors


def _twitter_player_error(page_path: object, meta: Metadata) -> CheckMessage | None:
    twitter = meta.twitter
    if twitter is None or twitter.card != "player" or twitter.player is not None:
        return None
    return Error(
        f"{page_path} folds twitter.card 'player' without twitter.player. Declare "
        "the player url, width and height, or pick another card.",
        obj=str(page_path),
        id="next.E104",
    )


@register(Tags.templates, NEXT, SEO)
def check_metadata_enum_values(*args, **kwargs) -> list[CheckMessage]:
    """Validate the enum values and the player card (`next.E104`).

    The settings tier and each page's own dict are read, the player card on the fold.
    """
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for item in declared_segments(pages):
        errors.extend(_enum_errors(item))
    for entry, meta in folded_pages(pages):
        player = _twitter_player_error(entry.page_path, meta)
        if player is not None:
            errors.append(player)
    return errors


@register(Tags.templates, NEXT, SEO)
def check_metadata_url_schemes(*args, **kwargs) -> list[CheckMessage]:
    """Flag a URL field whose scheme is neither http nor https (`next.E109`)."""
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry, meta in folded_pages(pages):
        for field, url in url_fields(meta):
            scheme = urlsplit(url).scheme
            if not scheme or scheme in WEB_SCHEMES:
                continue
            errors.append(
                Error(
                    f"{entry.page_path} folds metadata key {field!r} to {url!r}, "
                    f"whose scheme {scheme!r} is neither http nor https. Write an "
                    "absolute http(s) URL or a root-relative path.",
                    obj=str(entry.page_path),
                    id="next.E109",
                )
            )
    return errors


@register(Tags.templates, NEXT, SEO)
def check_metadata_hreflang_patterns(*args, **kwargs) -> list[CheckMessage]:
    """Warn when `alternates.languages=True` has no `i18n_patterns()` (`next.W087`).

    Without a language prefix every code translates to the same URL.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    prefixed: bool | None = None
    for entry, meta in folded_pages(pages):
        if meta.alternates is None or meta.alternates.languages is not True:
            continue
        if prefixed is None:
            prefixed = is_language_prefix_patterns_used(urlconf)[0]
        if prefixed:
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} asks for hreflang alternates with "
                f"alternates.languages=True, but ROOT_URLCONF "
                f"{urlconf!r} uses no i18n_patterns(), so every "
                "language points at the same URL. Wrap the page routes in "
                "i18n_patterns(), or list the alternates as a mapping.",
                obj=str(entry.page_path),
                id="next.W087",
            )
        )
    return warnings


@register(Tags.templates, NEXT, SEO)
def check_metadata_noindex_canonical(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a noindex page points its canonical at another origin (`next.W088`)."""
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in folded_pages(pages):
        canonical = meta.canonical
        if not isinstance(canonical, str) or not noindexed(meta):
            continue
        if not foreign_origin(canonical, site_url()):
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} is noindex and points its canonical at "
                f"{canonical!r}, another origin. A noindex page passes no signal to "
                "a cross-domain canonical, so drop the canonical or let the page be "
                "indexed.",
                obj=str(entry.page_path),
                id="next.W088",
            )
        )
    return warnings


__all__ = [
    "check_metadata_callable_returns_mapping",
    "check_metadata_enum_values",
    "check_metadata_hreflang_patterns",
    "check_metadata_noindex_canonical",
    "check_metadata_parent_parameter",
    "check_metadata_registration_files",
    "check_metadata_url_schemes",
    "check_page_metadata_shape",
]
