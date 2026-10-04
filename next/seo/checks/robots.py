"""System checks on the `/robots.txt` sources."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.urls import NoReverseMatch
from django.urls.converters import get_converters

from next.checks import NEXT, SEO
from next.seo.robots import declared_rules, robots_candidates, rule_pattern
from next.seo.sitemaps import SitemapOptions, listed_trails
from next.site.config import indexable_without_request
from next.urls.errors import URLParameterError
from next.urls.parser import default_url_parser
from next.urls.reverse import page_reverse

from .roots import (
    checked_noindex,
    items_trails,
    loaded_seo_roots,
    robots_modules,
    serves_sitemap,
    sitemap_roots,
)


if TYPE_CHECKING:
    import types
    from collections.abc import Sequence
    from pathlib import Path

    from next.seo.discovery import SeoRoot


SITEMAP_URL: Final = "/sitemap.xml"
_PARAMETER: Final = re.compile(r"<(?P<converter>[^>:]+):(?P<name>[^>]+)>")
_PLACEHOLDERS: Final = ("0", "x", "00000000-0000-0000-0000-000000000000")


def _single_source(url: str, paths: Sequence[Path]) -> list[CheckMessage]:
    """Return `next.E114` naming the source that answers `url` and the ones ignored."""
    if len(paths) <= 1:
        return []
    winner, *ignored = paths
    listed = ", ".join(str(path) for path in ignored)
    return [
        Error(
            f"{url} has {len(paths)} sources, and {winner} answers it while "
            f"{listed} is ignored. Keep one source across the page trees, "
            "deleting the ignored ones or merging them into the one that answers.",
            obj=str(winner),
            id="next.E114",
        )
    ]


@register(Tags.urls, NEXT, SEO)
def check_seo_single_sources(*args, **kwargs) -> list[CheckMessage]:
    """Require one `/robots.txt` source across every page tree (`next.E114`)."""
    roots = loaded_seo_roots()
    errors: list[CheckMessage] = []
    errors.extend(
        _single_source("/robots.txt", [path for path, _s in robots_candidates(roots)])
    )
    return errors


def _file_text(file_path: Path) -> tuple[str | None, CheckMessage | None]:
    """Decode a static text file, answering `next.E117` when it is not UTF-8."""
    try:
        text = file_path.read_bytes().decode("utf-8")
    except OSError:
        return None, None
    except UnicodeDecodeError as exc:
        return None, Error(
            f"{file_path} does not decode as UTF-8 ({exc.reason} at byte "
            f"{exc.start}), so crawlers misread its rules. Save the file as UTF-8.",
            obj=str(file_path),
            id="next.E117",
        )
    return text, None


def _names_sitemap(text: str) -> bool:
    lines = text.splitlines()
    return any(line.strip().lower().startswith("sitemap:") for line in lines)


@register(Tags.urls, NEXT, SEO)
def check_seo_text_files(*args, **kwargs) -> list[CheckMessage]:
    """Validate every static `robots.txt` (`next.E117`, `next.W098`).

    The files are served unchanged, so the framework cannot add a `Sitemap:` line.
    """
    roots = loaded_seo_roots()
    messages: list[CheckMessage] = []
    served = serves_sitemap()
    for root in roots:
        if root.robots_file is None:
            continue
        text, error = _file_text(root.robots_file)
        messages.extend(() if error is None else (error,))
        if text is None or not served or _names_sitemap(text):
            continue
        messages.append(
            DjangoWarning(
                f"{root.robots_file} names no Sitemap: line while the project "
                "serves /sitemap.xml, and a static file is served as written. Add "
                "'Sitemap: https://<host>/sitemap.xml', or switch to a robots.py "
                "that writes the line itself.",
                obj=str(root.robots_file),
                id="next.W098",
            )
        )
    return messages


def _reverse(trail: str, kwargs: dict[str, Any]) -> str | None:
    try:
        return page_reverse(trail, **kwargs)
    except NoReverseMatch:
        return None


def _route(trail: str) -> str | None:
    try:
        return default_url_parser.parse_url_pattern(trail)[0]
    except URLParameterError:
        return None


def _placeholder(converter_name: str) -> tuple[object, str] | None:
    """Return a value the named converter accepts, with its URL text."""
    converter = get_converters().get(converter_name)
    if converter is None:
        return None
    for text in _PLACEHOLDERS:
        if re.fullmatch(converter.regex, text) is None:
            continue
        try:
            return converter.to_python(text), text
        except ValueError:
            continue
    return None


def route_path(trail: str) -> str | None:
    """Return the URL path of `trail` up to its first parameter, `None` if unknown.

    Placeholder values fill the parameters, so a dynamic trail reverses on its own.
    """
    route = _route(trail)
    if route is None:
        return None
    kwargs: dict[str, Any] = {}
    filled = route
    for match in _PARAMETER.finditer(route):
        placeholder = _placeholder(match["converter"])
        if placeholder is None:
            return None
        kwargs[match["name"]], text = placeholder
        filled = filled.replace(match[0], text, 1)
    url = _reverse(trail, kwargs)
    if url is None or not url.endswith(filled):
        return None
    return url[: len(url) - len(filled)] + route.split("<", 1)[0]


def route_paths(root: SeoRoot) -> dict[str, str]:
    """Return the URL path of every trail, a dynamic one up to its first parameter."""
    paths: dict[str, str] = {}
    for trail in root.trails:
        path = route_path(trail)
        if path is not None:
            paths[trail] = path
    return paths


def _sitemap_paths(root: SeoRoot, module: types.ModuleType) -> set[str]:
    """Return the URL paths the sitemap of `root` lists."""
    exclude = SitemapOptions.read(module).exclude
    listed = set(listed_trails(root.trails, exclude, noindex=checked_noindex))
    listed |= items_trails(root)
    return {path for trail, path in route_paths(root).items() if trail in listed}


def _star_rules(module: types.ModuleType) -> tuple[list[str], list[str]]:
    """Return the `Allow` and `Disallow` values of the static groups that apply to `*`.

    A crawler named in a group of its own follows that group alone and skips `*`.
    """
    rules = [rule for rule in declared_rules(module) if "*" in rule.user_agents]
    allows = [prefix for rule in rules for prefix in rule.allow]
    disallows = [prefix for rule in rules for prefix in rule.disallow]
    return allows, disallows


def _covered(prefix: str, paths: set[str], allows: list[str]) -> list[str]:
    """Return the paths `prefix` blocks, minus those an equal or longer `Allow` matches.

    RFC 9309 applies the longest matching rule, and `Allow` takes precedence on a tie.
    """
    regex = rule_pattern(prefix)
    rivals = [rule_pattern(allow) for allow in allows if len(allow) >= len(prefix)]
    return sorted(
        path
        for path in paths
        if regex.match(path) and not any(rival.match(path) for rival in rivals)
    )


def _noindex_paths(roots: tuple[SeoRoot, ...]) -> set[str]:
    """Return the URL path of every routed page whose static metadata is noindex."""
    return {
        path
        for root in roots
        for trail, path in route_paths(root).items()
        if checked_noindex(root.trails[trail])
    }


def _listed_paths(roots: tuple[SeoRoot, ...]) -> set[str]:
    """Return every URL path the served sitemaps list, with `/sitemap.xml` itself."""
    if not serves_sitemap() or not indexable_without_request():
        return set()
    found = {SITEMAP_URL}
    for root, module in sitemap_roots(roots):
        found |= _sitemap_paths(root, module)
    return found


@register(Tags.urls, NEXT, SEO)
def check_robots_disallow(*args, **kwargs) -> list[CheckMessage]:
    """Compare the `Disallow` rules of static `*` groups with the listed URLs.

    `next.W095` flags a URL the sitemap lists, and `next.W096` a noindex page whose
    tag crawlers cannot read.
    """
    roots = loaded_seo_roots()
    warnings: list[CheckMessage] = []
    disallowed = [
        (path, prefix, allows)
        for path, module in robots_modules(roots)
        for allows, disallows in (_star_rules(module),)
        for prefix in disallows
    ]
    if not disallowed:
        return warnings
    listed = _listed_paths(roots)
    noindex = _noindex_paths(roots)
    for source, prefix, allows in disallowed:
        invited = _covered(prefix, listed, allows)
        if invited:
            warnings.append(
                DjangoWarning(
                    f"{source} disallows {prefix!r}, which covers "
                    f"{', '.join(invited)}, URLs the sitemap lists for crawlers. "
                    "Narrow the Disallow, or drop the URLs from the sitemap through "
                    "exclude.",
                    obj=str(source),
                    id="next.W095",
                )
            )
        hidden = _covered(prefix, noindex, allows)
        if hidden:
            warnings.append(
                DjangoWarning(
                    f"{source} disallows {prefix!r}, which covers the noindex pages "
                    f"{', '.join(hidden)}. A blocked crawler never reads the noindex, "
                    "so links from other sites keep the page indexed. Allow the "
                    "path and keep the noindex.",
                    obj=str(source),
                    id="next.W096",
                )
            )
    return warnings


__all__ = [
    "SITEMAP_URL",
    "check_robots_disallow",
    "check_seo_single_sources",
    "check_seo_text_files",
    "route_path",
    "route_paths",
]
