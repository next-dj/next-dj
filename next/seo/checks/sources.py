"""System checks on the SEO source files themselves."""

from __future__ import annotations

import ast
import os
import reprlib
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT, SEO
from next.pages.responses import cache_problems
from next.seo.discovery import SLUG, SOURCE_NAMES, SeoRoot, SeoSource
from next.seo.markers import CHANGEFREQS, RobotsRule, is_number
from next.seo.registry import sitemap_items_registry
from next.seo.robots import is_sitemap_url
from next.seo.sitemaps import MAX_LIMIT, SitemapOptions
from next.site import site_config
from next.utils import WEB_SCHEMES, is_int

from .roots import loaded_seo_roots, published_sources, robots_modules, sitemap_roots


if TYPE_CHECKING:
    import types
    from collections.abc import Iterator


def imports_future_annotations(file_path: Path) -> bool:
    """Whether the module source opens with `from __future__ import annotations`."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _import_error(source: SeoSource) -> CheckMessage | None:
    """Return `next.E110` naming the cause of a failed import."""
    if source.error is None:
        return None
    cause = source.error.__cause__
    return Error(
        f"{source.path} failed to import ({type(cause).__name__}: {cause}), so "
        "it declares nothing. Fix the module so its declarations reach the SEO "
        "routes.",
        obj=str(source.path),
        id="next.E110",
    )


def _sources(root: SeoRoot) -> Iterator[SeoSource]:
    """Yield the `sitemap.py` and the `robots.py` of the tree that exist."""
    for source in (root.sitemap, root.robots):
        if source is not None:
            yield source


@register(Tags.urls, NEXT, SEO)
def check_seo_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Require every Python SEO source to import (`next.E110`)."""
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
    for root in roots:
        errors.extend(
            error
            for source in _sources(root)
            if (error := _import_error(source)) is not None
        )
    return errors


@register(Tags.urls, NEXT, SEO)
def check_seo_module_annotations(*args, **kwargs) -> list[CheckMessage]:
    """Refuse deferred annotations in the Python SEO sources (`next.E119`).

    The dependency resolver reads the annotations of the callables of both sources.
    """
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
    for root in roots:
        for source in _sources(root):
            if not imports_future_annotations(source.path):
                continue
            errors.append(
                Error(
                    f"{source.path} imports annotations from __future__, which turns "
                    "the annotations of its callables into strings the dependency "
                    "resolver cannot read. Drop the import and keep them real.",
                    obj=str(source.path),
                    id="next.E119",
                )
            )
    return errors


def _is_string_list(value: object) -> bool:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) for item in value
    )


def _is_rules(value: object) -> bool:
    return callable(value) or (
        isinstance(value, list | tuple)
        and all(isinstance(item, RobotsRule) for item in value)
    )


def _is_sitemap_urls(value: object) -> bool:
    return isinstance(value, list | tuple) and all(map(is_sitemap_url, value))


type _Shape = tuple[Callable[[Any], bool], str]
type _Attribute = tuple[str, Callable[[Any], bool], str]

_CACHE_SHAPE: Final[_Shape] = (
    lambda value: not cache_problems(value, callable_allowed=False),
    "seconds as an int, False, or a valid next.pages.CacheDict",
)
_BOOL_SHAPE: Final[_Shape] = (lambda value: isinstance(value, bool), "a bool")
_SITEMAP_SHAPES: Final[dict[str, _Shape]] = {
    "changefreq": (
        lambda value: isinstance(value, str) and value in CHANGEFREQS,
        "one of " + ", ".join(sorted(CHANGEFREQS)),
    ),
    "priority": (
        lambda value: is_number(value) and 0 <= value <= 1,
        "a number from 0 to 1",
    ),
    "limit": (
        lambda value: is_int(value) and 0 < value <= MAX_LIMIT,
        f"an int from 1 to {MAX_LIMIT}",
    ),
    "cache": _CACHE_SHAPE,
    "exclude": (_is_string_list, "a list of trail globs"),
    "languages": (_is_string_list, "a list of language codes"),
    "i18n": _BOOL_SHAPE,
    "alternates": _BOOL_SHAPE,
    "x_default": _BOOL_SHAPE,
    "protocol": (
        lambda value: isinstance(value, str) and value in WEB_SCHEMES,
        "'http' or 'https'",
    ),
}
_SITEMAP_ATTRIBUTES: Final[tuple[_Attribute, ...]] = (
    *((field.name, *_SITEMAP_SHAPES[field.name]) for field in fields(SitemapOptions)),
    (
        "section",
        lambda value: isinstance(value, str) and SLUG.fullmatch(value) is not None,
        "a slug naming the sitemap section of the tree",
    ),
)
_ROBOTS_ATTRIBUTES: Final[tuple[_Attribute, ...]] = (
    (
        "rules",
        _is_rules,
        "a list of next.seo.RobotsRule, or a callable taking the request",
    ),
    (
        "sitemaps",
        _is_sitemap_urls,
        "a list of absolute http or https URLs on one line each",
    ),
    ("cache", *_CACHE_SHAPE),
)


def _shape_errors(
    path: Path, module: types.ModuleType, attributes: tuple[_Attribute, ...]
) -> list[CheckMessage]:
    """Return `next.E113` for every declared attribute outside its shape."""
    errors: list[CheckMessage] = []
    for name, accepts, expected in attributes:
        value = getattr(module, name, None)
        if value is None or accepts(value):
            continue
        errors.append(
            Error(
                f"{path} declares {name} = {reprlib.repr(value)}, expected "
                f"{expected}. The value is not read as written.",
                obj=str(path),
                id="next.E113",
            )
        )
    return errors


def _items_conflicts(path: Path) -> list[CheckMessage]:
    """Return `next.E128` for every trail a `sitemap.py` binds two callables to."""
    return [
        Error(
            f"{path} runs @sitemap.items twice for the trail {conflict.trail!r}, on "
            f"{conflict.replaced} and then on {conflict.kept}, so only "
            f"{conflict.kept} lists its URLs. Merge them into one callable.",
            obj=str(path),
            id="next.E128",
        )
        for conflict in sitemap_items_registry.conflicts()
        if conflict.file == path
    ]


@register(Tags.urls, NEXT, SEO)
def check_seo_module_attributes(*args, **kwargs) -> list[CheckMessage]:
    """Validate the module attributes of `sitemap.py` and `robots.py` (`next.E113`).

    Two `@sitemap.items` callables on one trail are `next.E128`.
    """
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
    for root, module in sitemap_roots(roots):
        errors.extend(_shape_errors(root.sitemap_path, module, _SITEMAP_ATTRIBUTES))
        errors.extend(_items_conflicts(root.sitemap_path))
    for path, module in robots_modules(roots):
        errors.extend(_shape_errors(path, module, _ROBOTS_ATTRIBUTES))
    return errors


@register(Tags.urls, NEXT, SEO)
def check_sitemap_items_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag an `@sitemap.items` run outside the root `sitemap.py` (`next.E118`).

    Only the registrations the `sitemap.py` at the top of a routed tree runs are read.
    """
    roots = loaded_seo_roots()
    if not roots:
        return []
    anchors = {root.sitemap.path for root in roots if root.sitemap is not None}
    registered = sitemap_items_registry.registered_names()
    return [
        Error(
            f"{file_path} runs @sitemap.items on {', '.join(registered[file_path])}, "
            "but only the sitemap.py at the top of a routed page tree is read, so no "
            "sitemap lists them. Run the decorator in that sitemap.py, which may "
            "import the callable.",
            obj=str(file_path),
            id="next.E118",
        )
        for file_path in sorted(registered, key=str)
        if file_path not in anchors
    ]


def _sources_below(root: SeoRoot) -> Iterator[Path]:
    """Yield every SEO source file below the top of the tree."""
    for dirpath, dirnames, filenames in os.walk(root.path):
        dirnames[:] = sorted(name for name in dirnames if name not in root.skip_names)
        if Path(dirpath) == root.path:
            continue
        for name in SOURCE_NAMES:
            if name in filenames:
                yield Path(dirpath) / name


@register(Tags.urls, NEXT, SEO)
def check_seo_sources_below_root(*args, **kwargs) -> list[CheckMessage]:
    """Warn about an SEO source below the top of its page tree (`next.W097`)."""
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    for root in roots:
        warnings.extend(
            DjangoWarning(
                f"{file_path} sits below the top of the page tree {root.path}, "
                f"where nothing reads it. Move it to {root.path / file_path.name}.",
                obj=str(file_path),
                id="next.W097",
            )
            for file_path in _sources_below(root)
        )
    return warnings


@register(NEXT, SEO, deploy=True)
def check_seo_sources_on_closed_site(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a site closed to search still publishes for crawlers (`next.W111`).

    A private site without a sitemap or a robots.txt raises no warning.
    """
    if site_config().indexable is not False:
        return []
    roots = loaded_seo_roots()
    sources = published_sources(roots)
    if not sources:
        return []
    return [
        DjangoWarning(
            "NEXT_FRAMEWORK['SITE']['INDEXABLE'] is False, so every page renders "
            f"noindex, yet the site publishes {' and '.join(sources)} for crawlers. "
            "Remove the key, answer per request through a callable, or drop the "
            "sources if the site is private.",
            obj=settings,
            id="next.W111",
        )
    ]


__all__ = [
    "check_seo_module_annotations",
    "check_seo_module_attributes",
    "check_seo_module_imports",
    "check_seo_sources_below_root",
    "check_seo_sources_on_closed_site",
    "check_sitemap_items_files",
    "imports_future_annotations",
]
