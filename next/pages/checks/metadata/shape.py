"""System checks for the metadata each routed `page.py` declares and folds to.

The ids are `next.E102` to `next.E109` and `next.W086` to `next.W088`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
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

from next.checks import NEXT
from next.checks.common import RegistrationSubject, registration_file_errors
from next.introspect import callable_name
from next.pages.checks.contexts import annotation_mismatch, load_routed_pages
from next.pages.manager import page
from next.pages.metadata import page_noindex
from next.utils import WEB_SCHEMES

from .links import foreign_origin, root_relative, url_fields
from .pages import MetadataPage, folded_pages, loaded_metadata_pages
from .scope import segment_errors


_TWITTER_CARDS: Final = frozenset({"summary", "summary_large_image", "app", "player"})

_METADATA_SUBJECT = RegistrationSubject(
    decorator="@page.metadata",
    anchor_name="page.py",
    render="page render",
    code="next.E106",
)


def _page_shape_errors(entry: MetadataPage) -> list[CheckMessage]:
    """Return `next.E102` to `next.E105` and the segment errors of one page."""
    errors: list[CheckMessage] = []
    page_path = entry.page_path
    obj = str(page_path)
    if entry.raw is not None and entry.entry is not None:
        errors.append(
            Error(
                f"{page_path} declares both a metadata dict and an @page.metadata "
                "callable. Keep one, the callable when the values depend on the "
                "request and the dict otherwise.",
                obj=obj,
                id="next.E102",
            )
        )
    if entry.raw is not None and not isinstance(entry.raw, Mapping):
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
    card = None if segment.twitter is None else segment.twitter.card
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


@register(Tags.templates, NEXT)
def check_page_metadata_shape(*args, **kwargs) -> list[CheckMessage]:
    """Validate the metadata dict of each routed page (`next.E102` to `next.E105`)."""
    init_errors, pages = loaded_metadata_pages()
    errors = list(init_errors)
    for entry in pages:
        errors.extend(_page_shape_errors(entry))
    return errors


@register(Tags.templates, NEXT)
def check_metadata_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@page.metadata` no page render collects (`next.E106`)."""
    init_errors, _loaded = load_routed_pages()
    if init_errors:
        return init_errors
    return registration_file_errors(
        _METADATA_SUBJECT,
        registrations=page._metadata_registry.registered_names(),
        misattributed=page._metadata_registry.misattributed(),
    )


@register(Tags.templates, NEXT)
def check_single_metadata_callable(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `page.py` registering more than one `@page.metadata` (`next.E107`).

    One slot holds the callable, so only the last registration runs.
    """
    init_errors, loaded = load_routed_pages()
    errors = list(init_errors)
    conflicts = page._metadata_registry.conflicts()
    for _url_path, page_path in loaded:
        names = conflicts.get(page_path)
        if not names:
            continue
        joined = ", ".join(names)
        errors.append(
            Error(
                f"page.py at {page_path} registers several @page.metadata callables "
                f"({joined}). Only the last one runs, so the earlier ones are "
                "ignored. Merge them into a single callable.",
                obj=str(page_path),
                id="next.E107",
            )
        )
    return errors


@register(Tags.templates, NEXT)
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


@register(Tags.templates, NEXT)
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


@register(Tags.templates, NEXT)
def check_metadata_absolute_urls(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a root-relative canonical or image has no base (`next.W086`).

    Only outside `DEBUG`, where the request host is what a crawler or a card sees.
    """
    if settings.DEBUG:
        return []
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in folded_pages(pages):
        if meta.base is not None:
            continue
        fields = [
            field
            for field, url in url_fields(meta)
            if field.startswith(("canonical", "og.images", "twitter.images"))
            and root_relative(url)
        ]
        if not fields:
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} folds {', '.join(fields)} to root-relative URLs "
                "while no base is set, so their absolute form follows the request "
                "host. Set base in NEXT_FRAMEWORK['METADATA']['DEFAULTS'] so "
                "crawlers and social cards see the canonical origin.",
                obj=str(entry.page_path),
                id="next.W086",
            )
        )
    return warnings


@register(Tags.templates, NEXT)
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


@register(Tags.templates, NEXT)
def check_metadata_noindex_canonical(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a noindex page points its canonical at another origin (`next.W088`)."""
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in folded_pages(pages):
        canonical = meta.canonical
        if not isinstance(canonical, str) or not page_noindex(meta):
            continue
        if not foreign_origin(canonical, meta.base):
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
    "check_metadata_absolute_urls",
    "check_metadata_callable_returns_mapping",
    "check_metadata_hreflang_patterns",
    "check_metadata_noindex_canonical",
    "check_metadata_registration_files",
    "check_metadata_url_schemes",
    "check_page_metadata_shape",
    "check_single_metadata_callable",
]
