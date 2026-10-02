"""System checks on what the `sitemap.py` of every page tree and each backend lists."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
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
from next.seo.backends import PageTreeSitemapBackend, backend_path
from next.seo.manager import seo_manager
from next.seo.sitemaps import (
    SitemapOptions,
    effective_limit,
    is_excluded,
    sitemap_languages,
    static_noindex,
)
from next.site.config import indexable_without_request
from next.utils import is_dynamic_trail, is_int

from .roots import declares_sitemap, items_trails, loaded_seo_roots, sitemap_roots


if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from pathlib import Path

    from next.seo.discovery import SeoRoot


_SITEMAP_TEMPLATES: Final = ("sitemap.xml", "sitemap_index.xml")


@register(Tags.urls, NEXT, SEO)
def check_sitemap_items_trails(*args, **kwargs) -> list[CheckMessage]:
    """Require every `@sitemap.items` trail to be routed by its tree (`next.E111`)."""
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
    for root, _module in sitemap_roots(roots):
        source = root.sitemap_path
        errors.extend(
            Error(
                f"@sitemap.items({entry.trail!r}) in {source} names a trail no page "
                f"under {root.path} routes, so the sitemap raises when built. "
                "Name the trail of a page.py in the tree, like 'posts/[slug]'.",
                obj=str(source),
                id="next.E111",
            )
            for entry in root.items_entries()
            if entry.trail not in root.trails
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


@register(Tags.templates, NEXT, SEO)
def check_sitemap_templates(*args, **kwargs) -> list[CheckMessage]:
    """Require the sitemap templates to load while a `sitemap.py` exists (`next.E112`).

    The XML comes from `django.contrib.sitemaps`, found only through `APP_DIRS`.
    """
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
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
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
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
                "order. Name each tree with section = '<slug>' in its sitemap.py "
                "for stable section addresses.",
                obj=str(group[1].path),
                id="next.W104",
            )
        )
    return warnings


@register(Tags.urls, NEXT, SEO)
def check_sitemap_dynamic_routes(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a dynamic route the sitemap neither lists nor excludes (`next.W097`).

    A statically noindex route never reaches the sitemap, so it needs neither.
    """
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    for root, module in sitemap_roots(roots):
        globs = SitemapOptions.read(module).exclude
        listed = items_trails(root)
        source = root.sitemap_path
        for trail, page_path in root.trails.items():
            if not is_dynamic_trail(trail) or trail in listed:
                continue
            if is_excluded(trail, globs) or static_noindex(page_path):
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

    Silent on a closed site, where no sitemap is served and every page reads noindex.
    """
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    if not indexable_without_request():
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


def _i18n_problems(options: SitemapOptions) -> list[str]:
    """Return what reads wrong among the i18n options of a `sitemap.py`, with a fix."""
    problems: list[str] = []
    if not options.i18n and (options.alternates or options.x_default):
        problems.append(
            "alternates and x_default take effect only with i18n = True. Set "
            "i18n = True, or drop alternates and x_default"
        )
    if options.x_default and not options.alternates:
        problems.append(
            "x_default takes effect only with alternates = True. Set "
            "alternates = True, or drop x_default"
        )
    known = {code for code, _name in settings.LANGUAGES}
    unknown = sorted(set(options.languages or ()) - known)
    if unknown:
        joined = ", ".join(repr(code) for code in unknown)
        problems.append(
            f"languages names {joined}, which settings.LANGUAGES lacks. Add the "
            "codes to settings.LANGUAGES, or drop them from languages"
        )
    return problems


def _limit_warning(source: Path, module: types.ModuleType) -> CheckMessage | None:
    """Return `next.W086` when a declared `limit` outgrows a page of alternates."""
    declared = getattr(module, "limit", None)
    options = SitemapOptions.read(module)
    if not is_int(declared) or declared <= 0:
        return None
    effective = effective_limit(options, sitemap_languages(options))
    if options.limit <= effective:
        return None
    links = len(sitemap_languages(options))
    default = " and x-default" if options.x_default else ""
    return DjangoWarning(
        f"{source} sets limit = {declared}, but every URL also links its "
        f"{links} languages{default} under alternates, so a page that long could "
        f"outgrow the 50 MB a sitemap may weigh, and pages hold {effective} URLs "
        f"instead. Lower limit to {effective} or less, or drop it.",
        obj=str(source),
        id="next.W086",
    )


def _uses_language_prefixes() -> bool:
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    return bool(is_language_prefix_patterns_used(urlconf)[0])


@register(Tags.urls, NEXT, SEO)
def check_sitemap_i18n_options(*args, **kwargs) -> list[CheckMessage]:
    """Warn about i18n options that take no effect or outgrow a page (W105, W106, W086).

    Without `i18n_patterns` every language reverses to one URL, listed once per code.
    """
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    for root, module in sitemap_roots(roots):
        options = SitemapOptions.read(module)
        source = root.sitemap_path
        warnings.extend(
            DjangoWarning(f"{source}: {problem}.", obj=str(source), id="next.W105")
            for problem in _i18n_problems(options)
        )
        limit = _limit_warning(source, module)
        warnings.extend(() if limit is None else (limit,))
        if options.i18n and not _uses_language_prefixes():
            warnings.append(
                DjangoWarning(
                    f"{source} sets i18n = True, but ROOT_URLCONF mounts no "
                    "i18n_patterns(), so every language lists the same URLs again. "
                    "Mount the pages under i18n_patterns() or drop i18n.",
                    obj=str(source),
                    id="next.W106",
                )
            )
    return warnings


@register(Tags.urls, NEXT, SEO)
def check_sitemap_excluded_items(*args, **kwargs) -> list[CheckMessage]:
    """Warn when `@sitemap.items` names a trail `exclude` drops (`next.W107`)."""
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    for root, module in sitemap_roots(roots):
        globs = SitemapOptions.read(module).exclude
        source = root.sitemap_path
        warnings.extend(
            DjangoWarning(
                f"@sitemap.items({trail!r}) in {source} lists URLs of a trail "
                "exclude covers, so the sitemap drops all of them. Narrow the "
                "glob, or drop the declaration.",
                obj=str(source),
                id="next.W107",
            )
            for trail in sorted(items_trails(root))
            if is_excluded(trail, globs)
        )
    return warnings


def _tree_sections(roots: tuple[SeoRoot, ...]) -> Iterator[tuple[str, str]]:
    """Yield every section the page trees serve with the source naming it."""
    for root, _module in sitemap_roots(roots):
        own = root.section
        yield own, str(root.sitemap_path)
        named = {
            entry.section
            for entry in root.items_entries()
            if entry.section is not None and entry.section != own
        }
        for name in sorted(named):
            yield name, f"@sitemap.items(section={name!r}) in {root.sitemap_path}"


def _backend_sections() -> tuple[list[tuple[str, str]], list[CheckMessage]]:
    """Return the sections every other backend serves, each with its backend.

    A backend whose `sections(None)` raises is `next.W089` and serves none here.
    """
    pairs: list[tuple[str, str]] = []
    warnings: list[CheckMessage] = []
    for backend in seo_manager.backends:
        if isinstance(backend, PageTreeSitemapBackend):
            continue
        name = backend_path(backend)
        try:
            sections = list(backend.sections(None))
        except Exception as exc:  # noqa: BLE001 - a backend is project code
            warnings.append(
                DjangoWarning(
                    f"{name}.sections(None) raised {type(exc).__name__}: {exc}, so "
                    "the checks cannot compare its sitemap sections with the "
                    "others. Make sections() work without a request, which is None "
                    "in a system check.",
                    obj=name,
                    id="next.W089",
                )
            )
            continue
        pairs.extend((section, name) for section in sections)
    return pairs, warnings


@register(Tags.urls, NEXT, SEO)
def check_sitemap_section_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Flag two sources serving one sitemap section name (`next.E116`, `next.W089`).

    The first source wins the name, so the second one never reaches the sitemap.
    """
    roots = loaded_seo_roots()
    backend_sections, errors = _backend_sections()
    owners: dict[str, str] = {}
    for section, source in (*_tree_sections(roots), *backend_sections):
        first = owners.setdefault(section, source)
        if first == source:
            continue
        errors.append(
            Error(
                f"{source} serves the sitemap section {section!r}, which {first} "
                "already serves, so only the first one is listed. Give one of them "
                "another section name.",
                obj=source,
                id="next.E116",
            )
        )
    return errors


__all__ = [
    "check_sitemap_dynamic_routes",
    "check_sitemap_excluded_items",
    "check_sitemap_i18n_options",
    "check_sitemap_items_trails",
    "check_sitemap_noindex_items",
    "check_sitemap_section_collisions",
    "check_sitemap_section_labels",
    "check_sitemap_templates",
]
