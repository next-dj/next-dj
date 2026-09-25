"""The opt-in SEO audits of the folded metadata, run by `check --deploy --tag seo`.

The ids are `next.W089` to `next.W096`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register
from django.urls import Resolver404, resolve
from django.utils import translation

from next.checks import NEXT, SEO
from next.pages.metadata import metadata_options
from next.utils import is_dynamic_trail

from .links import same_origin
from .pages import MetadataPage, folded_pages, loaded_metadata_pages, static_pages


_X_DEFAULT: Final = "x-default"
_DESCRIPTION_MIN: Final = 50
_DESCRIPTION_MAX: Final = 160
_TITLE_MAX: Final = 60
_DUPLICATE_GROUP: Final = 2


def _threshold(name: str, default: int) -> int:
    """Return an integer option of `METADATA['CHECKS']`, or `default` when unusable."""
    value = metadata_options().checks.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _require_description() -> bool:
    return bool(metadata_options().checks.get("REQUIRE_DESCRIPTION", True))


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_description(*args, **kwargs) -> list[CheckMessage]:
    """Audit the folded description of every static page (`next.W089`, `next.W092`).

    Skips a page a callable rewrites, and measures under the default language.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    longest = _threshold("DESCRIPTION_MAX", _DESCRIPTION_MAX)
    required = _require_description()
    with translation.override(settings.LANGUAGE_CODE):
        for entry, meta in static_pages(pages):
            if meta.description is None:
                if required:
                    warnings.append(_missing_description(entry))
                continue
            length = len(str(meta.description))
            if _DESCRIPTION_MIN <= length <= longest:
                continue
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} folds to a description of {length} "
                    f"characters, outside {_DESCRIPTION_MIN} to {longest}. Search "
                    "results truncate a long one and pad a short one with page "
                    "text.",
                    obj=str(entry.page_path),
                    id="next.W092",
                )
            )
    return warnings


def _missing_description(entry: MetadataPage) -> CheckMessage:
    return DjangoWarning(
        f"{entry.page_path} folds to no description, so search results write "
        "their own snippet. Declare description on the page or in "
        "NEXT_FRAMEWORK['METADATA']['DEFAULTS'], or set "
        "METADATA['CHECKS']['REQUIRE_DESCRIPTION'] to False.",
        obj=str(entry.page_path),
        id="next.W089",
    )


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_titles(*args, **kwargs) -> list[CheckMessage]:
    """Audit the folded titles of every static page (`next.W090`, `next.W091`).

    Skips a page a callable rewrites and compares the rest under the default language.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    longest = _threshold("TITLE_MAX", _TITLE_MAX)
    groups: dict[str, list[MetadataPage]] = {}
    with translation.override(settings.LANGUAGE_CODE):
        for entry, meta in static_pages(pages):
            if meta.title is None:
                continue
            text = str(meta.title)
            groups.setdefault(text, []).append(entry)
            if len(text) <= longest:
                continue
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} folds to the title {text!r} of {len(text)} "
                    f"characters, over {longest}. Search results cut it short, so "
                    "shorten the title or the template around it.",
                    obj=str(entry.page_path),
                    id="next.W091",
                )
            )
    for text, group in groups.items():
        if len(group) < _DUPLICATE_GROUP:
            continue
        trails = ", ".join(repr(f"/{entry.url_path}") for entry in group)
        warnings.append(
            DjangoWarning(
                f"Pages at {trails} fold to the same title {text!r}, so search "
                "results cannot tell them apart. Give each page a title of its own.",
                obj=str(group[0].page_path),
                id="next.W090",
            )
        )
    return warnings


def _resolves(path: str) -> bool:
    """Whether `path` resolves under the active language or any `LANGUAGES` code.

    Under `i18n_patterns` a prefixed path resolves only while its own language is on.
    """
    if _resolves_now(path or "/"):
        return True
    for code, _name in settings.LANGUAGES:
        with translation.override(code):
            if _resolves_now(path or "/"):
                return True
    return False


def _resolves_now(path: str) -> bool:
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_canonical(*args, **kwargs) -> list[CheckMessage]:
    """Audit literal canonicals (`next.W093`, `next.W094`).

    A literal on a dynamic route names one URL for every match of the route.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    for entry, meta in folded_pages(pages):
        canonical = meta.canonical
        if not isinstance(canonical, str):
            continue
        if is_dynamic_trail(entry.url_path):
            route = f"/{entry.url_path}"
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} on the dynamic route {route!r} declares the "
                    f"literal canonical {canonical!r}, so every match "
                    "claims the same URL. Set canonical=True for the self URL, or "
                    "compute it in a @page.metadata callable.",
                    obj=str(entry.page_path),
                    id="next.W094",
                )
            )
            continue
        if not same_origin(canonical, meta.base):
            continue
        if _resolves(urlsplit(canonical).path):
            continue
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} declares the canonical {canonical!r}, which "
                "resolves to no URL of this project. Point it at a routed path, or "
                "set canonical=True for the self URL.",
                obj=str(entry.page_path),
                id="next.W093",
            )
        )
    return warnings


@register(Tags.templates, NEXT, SEO, deploy=True)
def check_seo_alternates(*args, **kwargs) -> list[CheckMessage]:
    """Audit an hreflang mapping (`next.W095`, `next.W096`).

    A mapping is expected to name a fallback and only codes `LANGUAGES` lists.
    """
    init_errors, pages = loaded_metadata_pages()
    warnings = list(init_errors)
    known = {code for code, _name in settings.LANGUAGES}
    for entry, meta in folded_pages(pages):
        alternates = meta.alternates
        if alternates is None or not isinstance(alternates.languages, Mapping):
            continue
        languages = alternates.languages
        if _X_DEFAULT not in languages and alternates.x_default is None:
            warnings.append(
                DjangoWarning(
                    f"{entry.page_path} lists hreflang alternates without an "
                    "x-default, so a visitor outside those languages lands on none. "
                    "Add alternates.x_default, or an 'x-default' key to the mapping.",
                    obj=str(entry.page_path),
                    id="next.W095",
                )
            )
        unknown = sorted(
            code for code in languages if code != _X_DEFAULT and code not in known
        )
        if not unknown:
            continue
        joined = ", ".join(repr(code) for code in unknown)
        warnings.append(
            DjangoWarning(
                f"{entry.page_path} lists hreflang alternates for {joined}, which "
                "settings.LANGUAGES does not name. Add the language, or drop the "
                "alternate.",
                obj=str(entry.page_path),
                id="next.W096",
            )
        )
    return warnings


__all__ = [
    "check_seo_alternates",
    "check_seo_canonical",
    "check_seo_description",
    "check_seo_titles",
]
