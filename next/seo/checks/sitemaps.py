"""System checks on what the `sitemap.py` of every page tree lists.

The ids are `next.E111`, `next.E112`, `next.W097`, `next.W098` and `next.W104`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from next.checks import NEXT, SEO
from next.seo.sitemaps import (
    SitemapOptions,
    is_excluded,
    serves_sitemap,
    static_noindex,
)
from next.utils import is_dynamic_trail

from .roots import declares_sitemap, items_trails, loaded_seo_roots, sitemap_roots


if TYPE_CHECKING:
    from next.seo.discovery import SeoRoot


_SITEMAP_TEMPLATES: Final = ("sitemap.xml", "sitemap_index.xml")


@register(Tags.urls, NEXT, SEO)
def check_sitemap_items_trails(*args, **kwargs) -> list[CheckMessage]:
    """Require every `@sitemap.items` trail to be routed by its tree (`next.E111`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root, _module in sitemap_roots(roots):
        source = root.sitemap_path
        errors.extend(
            Error(
                f"@sitemap.items({trail!r}) in {source} names a trail no page "
                f"under {root.path} routes, so the sitemap raises when built. "
                "Name the trail of a page.py in the tree, like 'posts/[slug]'.",
                obj=str(source),
                id="next.E111",
            )
            for trail, _func in root.items_entries()
            if trail not in root.trails
        )
    return errors


def _missing_templates() -> list[str]:
    missing: list[str] = []
    for name in _SITEMAP_TEMPLATES:
        try:
            get_template(name)
        except TemplateDoesNotExist:
            missing.append(name)
    return missing


@register(Tags.urls, NEXT, SEO)
def check_sitemap_templates(*args, **kwargs) -> list[CheckMessage]:
    """Require the sitemap templates to load while a `sitemap.py` exists (`next.E112`).

    The XML comes from `django.contrib.sitemaps`, found only through `APP_DIRS`.
    """
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    if not declares_sitemap(roots):
        return errors
    missing = _missing_templates()
    if missing:
        errors.append(
            Error(
                f"The sitemap templates {', '.join(missing)} do not load, so "
                "/sitemap.xml fails to render. Add 'django.contrib.sitemaps' to "
                "INSTALLED_APPS and keep APP_DIRS=True on the Django template "
                "backend.",
                obj=settings,
                id="next.E112",
            )
        )
    return errors


@register(Tags.urls, NEXT, SEO)
def check_sitemap_section_labels(*args, **kwargs) -> list[CheckMessage]:
    """Warn when page trees share one sitemap section label (`next.W104`)."""
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    if not declares_sitemap(roots):
        return warnings
    groups: dict[str, list[SeoRoot]] = {}
    for root in roots:
        groups.setdefault(root.label, []).append(root)
    for label, group in groups.items():
        if len(group) == 1:
            continue
        listed = ", ".join(str(root.path) for root in group)
        served = ", ".join(root.section for root in group)
        warnings.append(
            DjangoWarning(
                f"Page trees {listed} share the sitemap section label {label!r}, so "
                f"the trees serve as the numbered sections {served}, in router "
                "order. Route them from differently named directories or different "
                "apps for stable section addresses.",
                obj=str(group[1].path),
                id="next.W104",
            )
        )
    return warnings


@register(Tags.urls, NEXT, SEO)
def check_sitemap_dynamic_routes(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a dynamic route the sitemap neither lists nor excludes (`next.W097`).

    A route with parameters has no URLs until `@sitemap.items` names them.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    for root, module in sitemap_roots(roots):
        globs = SitemapOptions.read(module).exclude
        listed = items_trails(root)
        source = root.sitemap_path
        for trail, page_path in root.trails.items():
            if not is_dynamic_trail(trail) or trail in listed:
                continue
            if is_excluded(trail, globs):
                continue
            warnings.append(
                DjangoWarning(
                    f"{page_path} routes the dynamic trail {trail!r}, for which the "
                    f"sitemap of {root.path} lists no URLs. Declare them with "
                    f"@sitemap.items({trail!r}) in {source}, or add a glob covering "
                    "the trail to exclude.",
                    obj=str(page_path),
                    id="next.W097",
                )
            )
    return warnings


@register(Tags.urls, NEXT, SEO)
def check_sitemap_noindex_items(*args, **kwargs) -> list[CheckMessage]:
    """Warn when `@sitemap.items` lists a trail whose page is noindex (`next.W098`).

    Silent under `NOINDEX`, where no sitemap is served and every page reads noindex.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    if not serves_sitemap(roots):
        return warnings
    for root, _module in sitemap_roots(roots):
        source = root.sitemap_path
        for trail in sorted(items_trails(root)):
            page_path = root.trails.get(trail)
            if page_path is None or not static_noindex(page_path):
                continue
            warnings.append(
                DjangoWarning(
                    f"{page_path} is noindex by its static metadata, but "
                    f"@sitemap.items({trail!r}) in {source} puts its URLs in the "
                    "sitemap. Drop the entries, or let the page be indexed.",
                    obj=str(page_path),
                    id="next.W098",
                )
            )
    return warnings


__all__ = [
    "check_sitemap_dynamic_routes",
    "check_sitemap_items_trails",
    "check_sitemap_noindex_items",
    "check_sitemap_section_labels",
    "check_sitemap_templates",
]
