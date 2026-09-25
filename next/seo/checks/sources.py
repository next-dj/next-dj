"""System checks on the `sitemap.py`, `robots.py` and `robots.txt` files themselves.

The ids are `next.E110`, `next.E113`, `next.E118` and `next.W102`.
"""

from __future__ import annotations

import ast
import os
import reprlib
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT, SEO
from next.seo.discovery import SOURCE_NAMES, SeoRoot, SeoSource
from next.seo.markers import Rule
from next.seo.registry import sitemap_items_registry
from next.seo.sitemaps import SitemapOptions
from next.utils import WEB_SCHEMES, is_bool, is_int

from .roots import loaded_seo_roots, robots_modules, sitemap_roots


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterator


_CHANGEFREQS: Final = frozenset(
    {"always", "hourly", "daily", "weekly", "monthly", "yearly", "never"}
)


def _imports_future_annotations(file_path: Path) -> bool:
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


def _import_errors(source: SeoSource, *, resolved: bool) -> Iterator[CheckMessage]:
    """Yield `next.E110` for a failed import, or deferred annotations when `resolved`.

    Only a `sitemap.py` runs callables through the dependency resolver.
    """
    if source.error is not None:
        cause = source.error.__cause__
        yield Error(
            f"{source.path} failed to import ({type(cause).__name__}: {cause}), so "
            "it declares nothing. Fix the module so its declarations reach the "
            "sitemap and robots routes.",
            obj=str(source.path),
            id="next.E110",
        )
    elif resolved and _imports_future_annotations(source.path):
        yield Error(
            f"{source.path} imports annotations from __future__, which turns the "
            "annotations of its callables into strings the dependency resolver "
            "cannot read. Drop the import and keep the annotations real.",
            obj=str(source.path),
            id="next.E110",
        )


@register(Tags.urls, NEXT, SEO)
def check_seo_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Require every `sitemap.py` and `robots.py` to import (`next.E110`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root in roots:
        if root.sitemap is not None:
            errors.extend(_import_errors(root.sitemap, resolved=True))
        if root.robots is not None:
            errors.extend(_import_errors(root.robots, resolved=False))
    return errors


def _is_string_list(value: object) -> bool:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) for item in value
    )


def _is_rule_list(value: object) -> bool:
    return isinstance(value, list | tuple) and all(
        isinstance(item, Rule) for item in value
    )


type _Shape = tuple[Callable[[Any], bool], str]
type _Attribute = tuple[str, Callable[[Any], bool], str]

_SITEMAP_SHAPES: Final[dict[str, _Shape]] = {
    "changefreq": (
        lambda value: isinstance(value, str) and value in _CHANGEFREQS,
        "one of " + ", ".join(sorted(_CHANGEFREQS)),
    ),
    "priority": (
        lambda value: (is_int(value) or isinstance(value, float)) and 0 <= value <= 1,
        "a number 0..1",
    ),
    "limit": (lambda value: is_int(value) and value > 0, "a positive int"),
    "cache": (lambda value: is_int(value) and value >= 0, "seconds as an int, no bool"),
    "exclude": (_is_string_list, "a list of trail globs"),
    "languages": (_is_string_list, "a list of language codes"),
    "i18n": (is_bool, "a bool"),
    "alternates": (is_bool, "a bool"),
    "x_default": (is_bool, "a bool"),
    "protocol": (
        lambda value: isinstance(value, str) and value in WEB_SCHEMES,
        "'http' or 'https'",
    ),
}
_SITEMAP_ATTRIBUTES: Final[tuple[_Attribute, ...]] = tuple(
    (field.name, *_SITEMAP_SHAPES[field.name]) for field in fields(SitemapOptions)
)
_ROBOTS_ATTRIBUTES: Final[tuple[_Attribute, ...]] = (
    ("rules", _is_rule_list, "a list of next.seo.Rule"),
    ("host", lambda value: isinstance(value, str), "a string"),
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


@register(Tags.urls, NEXT, SEO)
def check_seo_module_attributes(*args, **kwargs) -> list[CheckMessage]:
    """Validate the module attributes of `sitemap.py` and `robots.py` (`next.E113`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root, module in sitemap_roots(roots):
        errors.extend(_shape_errors(root.sitemap_path, module, _SITEMAP_ATTRIBUTES))
    for path, module in robots_modules(roots):
        errors.extend(_shape_errors(path, module, _ROBOTS_ATTRIBUTES))
    return errors


@register(Tags.urls, NEXT, SEO)
def check_sitemap_items_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag an `@sitemap.items` run outside the root `sitemap.py` (`next.E118`).

    Only the registrations the `sitemap.py` at the top of a routed tree runs are read.
    """
    init_errors, roots = loaded_seo_roots()
    if init_errors:
        return init_errors
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
    """Yield every SEO source file sitting under, but not at, the top of the tree."""
    for dirpath, dirnames, filenames in os.walk(root.path):
        dirnames[:] = sorted(name for name in dirnames if name not in root.skip_names)
        if Path(dirpath) == root.path:
            continue
        for name in SOURCE_NAMES:
            if name in filenames:
                yield Path(dirpath) / name


@register(Tags.urls, NEXT, SEO)
def check_seo_sources_below_root(*args, **kwargs) -> list[CheckMessage]:
    """Warn about an SEO source below the top of its page tree (`next.W102`)."""
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    for root in roots:
        warnings.extend(
            DjangoWarning(
                f"{file_path} sits below the top of the page tree {root.path}, "
                f"where nothing reads it. Move it to {root.path / file_path.name}.",
                obj=str(file_path),
                id="next.W102",
            )
            for file_path in _sources_below(root)
        )
    return warnings


__all__ = [
    "check_seo_module_attributes",
    "check_seo_module_imports",
    "check_seo_sources_below_root",
    "check_sitemap_items_files",
]
