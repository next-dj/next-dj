"""System checks on the `/robots.txt` sources of the page trees.

The ids are `next.E114`, `next.E117`, `next.W100`, `next.W101` and `next.W103`.
"""

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
from next.seo.robots import declared_rules
from next.seo.sitemaps import (
    SitemapOptions,
    listed_trails,
    serves_sitemap,
    static_noindex,
)
from next.urls.errors import URLParameterError
from next.urls.parser import default_url_parser
from next.urls.reverse import page_reverse

from .roots import (
    items_trails,
    loaded_seo_roots,
    robots_modules,
    robots_paths,
    sitemap_roots,
)


if TYPE_CHECKING:
    import types
    from pathlib import Path

    from next.seo.discovery import SeoRoot


SITEMAP_URL: Final = "/sitemap.xml"
_PARAMETER: Final = re.compile(r"<(?P<converter>[^>:]+):(?P<name>[^>]+)>")
_PLACEHOLDERS: Final = ("0", "x", "00000000-0000-0000-0000-000000000000")


@register(Tags.urls, NEXT, SEO)
def check_robots_single_source(*args, **kwargs) -> list[CheckMessage]:
    """Require one source for `/robots.txt` across every page tree (`next.E114`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    paths = robots_paths(roots)
    if len(paths) > 1:
        listed = ", ".join(str(path) for path in paths)
        errors.append(
            Error(
                f"/robots.txt has {len(paths)} sources and only {paths[0]} answers "
                f"it: {listed}. Keep one robots.py or robots.txt in one page tree.",
                obj=str(paths[0]),
                id="next.E114",
            )
        )
    return errors


def _robots_text(file_path: Path) -> tuple[str | None, CheckMessage | None]:
    """Decode a static `robots.txt`, answering `next.E117` when it is not UTF-8."""
    try:
        text = file_path.read_bytes().decode("utf-8")
    except OSError:
        return None, None
    except UnicodeDecodeError as exc:
        return None, Error(
            f"{file_path} does not decode as UTF-8 ({exc.reason} at byte "
            f"{exc.start}), so crawlers read garbage. Save the file as UTF-8.",
            obj=str(file_path),
            id="next.E117",
        )
    return text, None


def _names_sitemap(text: str) -> bool:
    lines = text.splitlines()
    return any(line.strip().lower().startswith("sitemap:") for line in lines)


@register(Tags.urls, NEXT, SEO)
def check_robots_file(*args, **kwargs) -> list[CheckMessage]:
    """Read every static `robots.txt` (`next.E117`, `next.W103`).

    The file is served byte for byte, so a missing `Sitemap:` line stays missing.
    """
    init_errors, roots = loaded_seo_roots()
    messages = list(init_errors)
    served = serves_sitemap(roots)
    for root in roots:
        if root.robots_file is None:
            continue
        text, error = _robots_text(root.robots_file)
        if error is not None:
            messages.append(error)
        if text is None or not served or _names_sitemap(text):
            continue
        messages.append(
            DjangoWarning(
                f"{root.robots_file} names no Sitemap: line while the project "
                "serves /sitemap.xml, and a static file is served as written. Add "
                "'Sitemap: https://<host>/sitemap.xml', or switch to a robots.py "
                "that writes the line itself.",
                obj=str(root.robots_file),
                id="next.W103",
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
    """Return a value the named converter takes, with the text it reverses to."""
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


def _route_path(trail: str) -> str | None:
    """Return the URL path of `trail` cut at its first parameter, or `None` if unknown.

    Placeholders fill the parameters, so a trail reverses without a static sibling.
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
    """Return the URL path of every trail, a dynamic one cut at its first parameter."""
    paths: dict[str, str] = {}
    for trail in root.trails:
        path = _route_path(trail)
        if path is not None:
            paths[trail] = path
    return paths


def _sitemap_paths(root: SeoRoot, module: types.ModuleType) -> set[str]:
    """Return the URL paths the sitemap of `root` lists."""
    listed = set(listed_trails(root.trails, SitemapOptions.read(module).exclude))
    listed |= items_trails(root)
    return {path for trail, path in route_paths(root).items() if trail in listed}


def _disallow_regex(prefix: str) -> re.Pattern[str]:
    """Compile a `Disallow` value, `*` matching anything and a final `$` anchoring."""
    anchored = prefix.endswith("$")
    body = ".*".join(re.escape(part) for part in prefix.removesuffix("$").split("*"))
    return re.compile(body + ("$" if anchored else ""))


def _disallows(module: types.ModuleType) -> list[str]:
    """Return every non-empty `Disallow` value the rules declare, in order."""
    return [
        prefix
        for rule in declared_rules(module)
        for prefix in rule.disallow
        if isinstance(prefix, str) and prefix
    ]


def _covered(prefix: str, paths: set[str]) -> list[str]:
    regex = _disallow_regex(prefix)
    return sorted(path for path in paths if regex.match(path))


def _noindex_paths(roots: tuple[SeoRoot, ...]) -> set[str]:
    """Return the URL path of every routed page whose static metadata is noindex."""
    return {
        path
        for root in roots
        for trail, path in route_paths(root).items()
        if static_noindex(root.trails[trail])
    }


def _listed_paths(roots: tuple[SeoRoot, ...]) -> set[str]:
    """Return every URL path the served sitemaps list, with `/sitemap.xml` itself."""
    if not serves_sitemap(roots):
        return set()
    found = {SITEMAP_URL}
    for root, module in sitemap_roots(roots):
        found |= _sitemap_paths(root, module)
    return found


@register(Tags.urls, NEXT, SEO)
def check_robots_disallow(*args, **kwargs) -> list[CheckMessage]:
    """Read every `Disallow` against the sitemap and the noindex pages.

    `next.W100` flags a listed URL, `next.W101` a noindex page whose tag goes unread.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    disallowed = [
        (path, prefix)
        for path, module in robots_modules(roots)
        for prefix in _disallows(module)
    ]
    if not disallowed:
        return warnings
    listed = _listed_paths(roots)
    noindex = _noindex_paths(roots)
    for source, prefix in disallowed:
        invited = _covered(prefix, listed)
        if invited:
            warnings.append(
                DjangoWarning(
                    f"{source} disallows {prefix!r}, which covers "
                    f"{', '.join(invited)}, URLs the sitemap invites crawlers to. "
                    "Narrow the Disallow, or drop the URLs from the sitemap through "
                    "exclude.",
                    obj=str(source),
                    id="next.W100",
                )
            )
        hidden = _covered(prefix, noindex)
        if hidden:
            warnings.append(
                DjangoWarning(
                    f"{source} disallows {prefix!r}, which covers the noindex pages "
                    f"{', '.join(hidden)}. A crawler kept out never sees the "
                    "noindex, so the page stays indexed from outside links. Let the "
                    "crawler in and keep the noindex.",
                    obj=str(source),
                    id="next.W101",
                )
            )
    return warnings


__all__ = [
    "SITEMAP_URL",
    "check_robots_disallow",
    "check_robots_file",
    "check_robots_single_source",
    "route_paths",
]
