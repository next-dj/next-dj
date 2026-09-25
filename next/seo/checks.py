"""System checks for the `sitemap.py`, `robots.py` and `robots.txt` of every page tree.

The ids are `next.E110` to `next.E117` and `next.W097` to `next.W103`.
"""

from __future__ import annotations

import ast
import os
import re
import reprlib
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

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
from django.urls import NoReverseMatch, Resolver404, ResolverMatch, resolve

from next.checks import NEXT
from next.checks.common import (
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.pages import page
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.loaders import load_page_module
from next.urls.errors import URLParameterError
from next.urls.parser import default_url_parser
from next.urls.reverse import page_reverse
from next.utils import walk_page_tree

from .discovery import ROBOTS_FILE, ROBOTS_MODULE, SITEMAP_MODULE, section_label
from .markers import Rule
from .registry import sitemap_items_registry
from .sitemaps import is_noindex
from .views import robots as robots_view, sitemap as sitemap_view


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterator

    from next.pages.errors import PageModuleImportError


_DYNAMIC_MARK: Final = "["
_SITEMAP_URL: Final = "/sitemap.xml"
_ROBOTS_URL: Final = "/robots.txt"
_SITEMAP_TEMPLATES: Final = ("sitemap.xml", "sitemap_index.xml")
_CHANGEFREQS: Final = frozenset(
    {"always", "hourly", "daily", "weekly", "monthly", "yearly", "never"}
)
_PROTOCOLS: Final = frozenset({"http", "https"})
_SINGLE: Final = 1


class SeoSource(NamedTuple):
    """One `sitemap.py` or `robots.py` with the module it loaded to, or its failure."""

    path: Path
    module: types.ModuleType | None
    error: PageModuleImportError | None


class CheckedRoot(NamedTuple):
    """One page tree with its routed trails and the SEO sources found at its top."""

    path: Path
    section: str
    trails: dict[str, Path]
    sitemap: SeoSource | None
    robots: SeoSource | None
    robots_file: Path | None
    skip_names: frozenset[str]


def _source(file_path: Path) -> SeoSource | None:
    """Load the module at `file_path`, or answer `None` when no file sits there."""
    if not file_path.is_file():
        return None
    module, error = load_page_module(file_path)
    return SeoSource(file_path, module, error)


def _checked_root(root_path: Path, skip_names: frozenset[str]) -> CheckedRoot:
    robots_file = root_path / ROBOTS_FILE
    return CheckedRoot(
        path=root_path,
        section=section_label(root_path),
        trails=dict(walk_page_tree(root_path, skip_names)),
        sitemap=_source(root_path / SITEMAP_MODULE),
        robots=_source(root_path / ROBOTS_MODULE),
        robots_file=robots_file if robots_file.is_file() else None,
        skip_names=skip_names,
    )


def loaded_seo_roots() -> tuple[list[CheckMessage], list[CheckedRoot]]:
    """Return every routed page tree with its SEO sources loaded, once per tree."""
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors, []
    seen: set[Path] = set()
    roots: list[CheckedRoot] = []
    for router in router_manager.backends:
        skip_names = page_tree_skip_names(router)
        roots.extend(
            _checked_root(root.path, skip_names)
            for root in get_page_roots(router)
            if first_visit(root.path, seen)
        )
    return init_errors, roots


def _sitemap_roots(roots: list[CheckedRoot]) -> Iterator[tuple[CheckedRoot, Any]]:
    """Yield every tree whose `sitemap.py` imported, paired with the module."""
    for root in roots:
        if root.sitemap is not None and root.sitemap.module is not None:
            yield root, root.sitemap.module


def _robots_roots(roots: list[CheckedRoot]) -> Iterator[tuple[CheckedRoot, Any]]:
    """Yield every tree whose `robots.py` imported, paired with the module."""
    for root in roots:
        if root.robots is not None and root.robots.module is not None:
            yield root, root.robots.module


def _has_sitemap(roots: list[CheckedRoot]) -> bool:
    return any(root.sitemap is not None for root in roots)


def _robots_paths(roots: list[CheckedRoot]) -> list[Path]:
    """Return every robots source in the order the runtime prefers them."""
    return [
        path
        for root in roots
        for path in (
            None if root.robots is None else root.robots.path,
            root.robots_file,
        )
        if path is not None
    ]


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


def _import_error(source: SeoSource) -> CheckMessage | None:
    """Return `next.E110` for a source that failed to import or defers annotations."""
    if source.error is not None:
        cause = source.error.__cause__
        return Error(
            f"{source.path} failed to import ({type(cause).__name__}: {cause}), so "
            "it declares nothing. Fix the module so its declarations reach the "
            "sitemap and robots routes.",
            obj=str(source.path),
            id="next.E110",
        )
    if _imports_future_annotations(source.path):
        return Error(
            f"{source.path} imports annotations from __future__, which turns the "
            "annotations of its callables into strings the dependency resolver "
            "cannot read. Drop the import and keep the annotations real.",
            obj=str(source.path),
            id="next.E110",
        )
    return None


@register(Tags.urls, NEXT)
def check_seo_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Require every `sitemap.py` and `robots.py` to import (`next.E110`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root in roots:
        for source in (root.sitemap, root.robots):
            if source is None:
                continue
            error = _import_error(source)
            if error is not None:
                errors.append(error)
    return errors


@register(Tags.urls, NEXT)
def check_sitemap_items_trails(*args, **kwargs) -> list[CheckMessage]:
    """Require every `@sitemap.items` trail to be routed by its tree (`next.E111`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root, _module in _sitemap_roots(roots):
        source = root.path / SITEMAP_MODULE
        for trail, _func in sitemap_items_registry.entries_for(root.path):
            if trail in root.trails:
                continue
            errors.append(
                Error(
                    f"@sitemap.items({trail!r}) in {source} names a trail no page "
                    f"under {root.path} routes, so the sitemap raises when built. "
                    "Name the trail of a page.py in the tree, like 'posts/[slug]'.",
                    obj=str(source),
                    id="next.E111",
                )
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


@register(Tags.urls, NEXT)
def check_sitemap_templates(*args, **kwargs) -> list[CheckMessage]:
    """Require the sitemap templates to load while a `sitemap.py` exists (`next.E112`).

    The XML comes from `django.contrib.sitemaps`, found only through `APP_DIRS`.
    """
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    if not _has_sitemap(roots):
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


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list | tuple) and all(
        isinstance(item, str) for item in value
    )


def _is_rule_list(value: object) -> bool:
    return isinstance(value, list | tuple) and all(
        isinstance(item, Rule) for item in value
    )


type _Shape = tuple[str, Callable[[Any], bool], str]

_SITEMAP_SHAPES: Final[tuple[_Shape, ...]] = (
    (
        "changefreq",
        lambda value: isinstance(value, str) and value in _CHANGEFREQS,
        "one of " + ", ".join(sorted(_CHANGEFREQS)),
    ),
    ("priority", lambda value: _is_number(value) and 0 <= value <= 1, "a number 0..1"),
    ("limit", lambda value: _is_int(value) and value > 0, "a positive int"),
    ("cache", lambda value: _is_int(value) and value >= 0, "seconds as an int"),
    ("exclude", _is_string_list, "a list of trail globs"),
    ("languages", _is_string_list, "a list of language codes"),
    ("i18n", _is_bool, "a bool"),
    ("alternates", _is_bool, "a bool"),
    ("x_default", _is_bool, "a bool"),
    (
        "protocol",
        lambda value: isinstance(value, str) and value in _PROTOCOLS,
        "'http' or 'https'",
    ),
)
_ROBOTS_SHAPES: Final[tuple[_Shape, ...]] = (
    ("rules", _is_rule_list, "a list of next.seo.Rule"),
    ("host", lambda value: isinstance(value, str), "a string"),
)


def _shape_errors(source: SeoSource, shapes: tuple[_Shape, ...]) -> list[CheckMessage]:
    """Return `next.E113` for every declared attribute outside its shape."""
    errors: list[CheckMessage] = []
    for name, accepts, expected in shapes:
        value = getattr(source.module, name, None)
        if value is None or accepts(value):
            continue
        errors.append(
            Error(
                f"{source.path} declares {name} = {reprlib.repr(value)}, expected "
                f"{expected}. The value is not read as written.",
                obj=str(source.path),
                id="next.E113",
            )
        )
    return errors


@register(Tags.urls, NEXT)
def check_seo_module_attributes(*args, **kwargs) -> list[CheckMessage]:
    """Validate the module attributes of `sitemap.py` and `robots.py` (`next.E113`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root in roots:
        if root.sitemap is not None and root.sitemap.module is not None:
            errors.extend(_shape_errors(root.sitemap, _SITEMAP_SHAPES))
        if root.robots is not None and root.robots.module is not None:
            errors.extend(_shape_errors(root.robots, _ROBOTS_SHAPES))
    return errors


@register(Tags.urls, NEXT)
def check_robots_single_source(*args, **kwargs) -> list[CheckMessage]:
    """Require one source for `/robots.txt` across every page tree (`next.E114`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    paths = _robots_paths(roots)
    if len(paths) > _SINGLE:
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


def _resolved(url: str) -> ResolverMatch | None:
    """Return what `ROOT_URLCONF` resolves `url` to, or `None` for a miss."""
    try:
        return resolve(url)
    except Resolver404:
        return None


def _served(roots: list[CheckedRoot]) -> list[tuple[str, Callable[..., Any], str]]:
    """Return the URLs the sources call for, each with its view and its source."""
    served: list[tuple[str, Callable[..., Any], str]] = []
    if _has_sitemap(roots):
        served.append((_SITEMAP_URL, sitemap_view, "a sitemap.py"))
    if _robots_paths(roots):
        served.append((_ROBOTS_URL, robots_view, "a robots source"))
    return served


def _trail_collides(trail: str, roots: list[CheckedRoot]) -> str | None:
    """Return the served address a page trail takes, or `None` when it takes none."""
    if _has_sitemap(roots) and (
        trail == "sitemap.xml" or fnmatch(trail, "sitemap-*.xml")
    ):
        return f"/{trail}"
    if trail == "robots.txt" and _robots_paths(roots):
        return _ROBOTS_URL
    return None


@register(Tags.urls, NEXT)
def check_seo_route_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Flag a page or a urlpattern on an address the sources serve (`next.E115`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    for root in roots:
        for trail, page_path in root.trails.items():
            address = _trail_collides(trail, roots)
            if address is None:
                continue
            errors.append(
                Error(
                    f"{page_path} routes {address}, the address the framework serves "
                    "from the SEO sources, so the two answers shadow each other. "
                    "Rename the directory.",
                    obj=str(page_path),
                    id="next.E115",
                )
            )
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    for url, view, _source in _served(roots):
        match = _resolved(url)
        if match is None or match.func is view:
            continue
        errors.append(
            Error(
                f"ROOT_URLCONF {urlconf!r} resolves {url} to {match.view_name}, "
                "ahead of the route the framework serves it at. Move that pattern "
                "below include('next.urls'), or drop it.",
                obj=settings,
                id="next.E115",
            )
        )
    return errors


@register(Tags.urls, NEXT)
def check_sitemap_section_labels(*args, **kwargs) -> list[CheckMessage]:
    """Require the page trees to take distinct sitemap section labels (`next.E116`)."""
    init_errors, roots = loaded_seo_roots()
    errors = list(init_errors)
    if not _has_sitemap(roots):
        return errors
    groups: dict[str, list[CheckedRoot]] = {}
    for root in roots:
        groups.setdefault(root.section, []).append(root)
    for label, group in groups.items():
        if len(group) <= _SINGLE:
            continue
        listed = ", ".join(str(root.path) for root in group)
        errors.append(
            Error(
                f"Page trees {listed} share the sitemap section label {label!r}, so "
                f"the later ones serve as {label}-2 and on. Route them from "
                "differently named directories, or from different apps.",
                obj=str(group[1].path),
                id="next.E116",
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


@register(Tags.urls, NEXT)
def check_robots_file(*args, **kwargs) -> list[CheckMessage]:
    """Read every static `robots.txt` (`next.E117`, `next.W103`).

    The file is served byte for byte, so a missing `Sitemap:` line stays missing.
    """
    init_errors, roots = loaded_seo_roots()
    messages = list(init_errors)
    has_sitemap = _has_sitemap(roots)
    for root in roots:
        if root.robots_file is None:
            continue
        text, error = _robots_text(root.robots_file)
        if error is not None:
            messages.append(error)
        if text is None or not has_sitemap or _names_sitemap(text):
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


def _exclude_globs(module: types.ModuleType) -> tuple[str, ...]:
    """Return the string globs of `exclude`, the way the sitemap reads them."""
    declared = getattr(module, "exclude", ())
    if not isinstance(declared, list | tuple):
        return ()
    return tuple(item for item in declared if isinstance(item, str))


def _items_trails(root: CheckedRoot) -> set[str]:
    return {trail for trail, _func in sitemap_items_registry.entries_for(root.path)}


@register(Tags.urls, NEXT)
def check_sitemap_dynamic_routes(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a dynamic route the sitemap neither lists nor excludes (`next.W097`).

    A route with parameters has no URLs until `@sitemap.items` names them.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    for root, module in _sitemap_roots(roots):
        globs = _exclude_globs(module)
        listed = _items_trails(root)
        source = root.path / SITEMAP_MODULE
        for trail, page_path in root.trails.items():
            if _DYNAMIC_MARK not in trail or trail in listed:
                continue
            if any(fnmatch(trail, glob) for glob in globs):
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


def _static_noindex(page_path: Path) -> bool:
    """Whether the static metadata of a page keeps it out of the index.

    A chain the schema refuses is another check's finding, so it reads as indexed.
    """
    try:
        return is_noindex(page.static_metadata(page_path))
    except (PageMetadataShapeError, PageMetadataConflictError):
        return False


@register(Tags.urls, NEXT)
def check_sitemap_noindex_items(*args, **kwargs) -> list[CheckMessage]:
    """Warn when `@sitemap.items` lists a trail whose page is noindex (`next.W098`)."""
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    for root, _module in _sitemap_roots(roots):
        source = root.path / SITEMAP_MODULE
        for trail in sorted(_items_trails(root)):
            page_path = root.trails.get(trail)
            if page_path is None or not _static_noindex(page_path):
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


@register(Tags.urls, NEXT)
def check_seo_routes_at_host_root(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a declared SEO route is not at the host root (`next.W099`).

    A `next.urls` include under a prefix or `i18n_patterns()` moves the routes with it.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    urlconf = str(getattr(settings, "ROOT_URLCONF", ""))
    for url, _view, source in _served(roots):
        if _resolved(url) is not None:
            continue
        warnings.append(
            DjangoWarning(
                f"{url} does not resolve under ROOT_URLCONF {urlconf!r} although "
                f"{source} declares it, so crawlers find nothing at the host root. "
                "Mount include('next.seo.urls') at the root of the URLconf, outside "
                "any prefix and i18n_patterns().",
                obj=settings,
                id="next.W099",
            )
        )
    return warnings


def _reverse(trail: str) -> str | None:
    try:
        return page_reverse(trail)
    except NoReverseMatch:
        return None


def _route(trail: str) -> str | None:
    try:
        return default_url_parser.parse_url_pattern(trail)[0]
    except URLParameterError:
        return None


def _route_paths(root: CheckedRoot) -> dict[str, str]:
    """Return the URL path of every trail, a dynamic one cut at its first parameter.

    The static routes reverse, and the mount they reverse under places the dynamic
    ones, whose parameters no reverse can fill.
    """
    paths: dict[str, str] = {}
    mount: str | None = None
    for trail in root.trails:
        if _DYNAMIC_MARK in trail:
            continue
        url = _reverse(trail)
        route = _route(trail)
        if url is None or route is None:
            continue
        paths[trail] = url
        if mount is None and url.endswith(route):
            mount = url[: len(url) - len(route)]
    if mount is None:
        return paths
    for trail in root.trails:
        route = _route(trail)
        if _DYNAMIC_MARK in trail and route is not None:
            paths[trail] = mount + route.split("<", 1)[0]
    return paths


def _sitemap_paths(root: CheckedRoot, module: types.ModuleType) -> dict[str, str]:
    """Return the URL paths the sitemap of `root` lists, keyed by trail."""
    globs = _exclude_globs(module)
    listed = _items_trails(root)
    paths = _route_paths(root)
    return {
        trail: path
        for trail, path in paths.items()
        if trail in listed
        or (
            _DYNAMIC_MARK not in trail
            and not any(fnmatch(trail, glob) for glob in globs)
            and not _static_noindex(root.trails[trail])
        )
    }


def _disallow_regex(prefix: str) -> re.Pattern[str]:
    """Compile a `Disallow` value, `*` matching anything and a final `$` anchoring."""
    anchored = prefix.endswith("$")
    body = ".*".join(re.escape(part) for part in prefix.removesuffix("$").split("*"))
    return re.compile(body + ("$" if anchored else ""))


def _disallows(module: types.ModuleType) -> list[str]:
    """Return every non-empty `Disallow` value the rules declare, in order."""
    declared = getattr(module, "rules", ())
    if not isinstance(declared, list | tuple):
        return []
    return [
        prefix
        for rule in declared
        if isinstance(rule, Rule)
        for prefix in rule.disallow
        if isinstance(prefix, str) and prefix
    ]


def _covered(prefix: str, paths: dict[str, str]) -> list[str]:
    regex = _disallow_regex(prefix)
    return sorted({path for path in paths.values() if regex.match(path)})


def _noindex_paths(roots: list[CheckedRoot]) -> dict[str, str]:
    """Return the URL path of every routed page whose static metadata is noindex."""
    found: dict[str, str] = {}
    for root in roots:
        for trail, path in _route_paths(root).items():
            if _static_noindex(root.trails[trail]):
                found[f"{root.path}:{trail}"] = path
    return found


def _listed_paths(roots: list[CheckedRoot]) -> dict[str, str]:
    """Return every URL path the sitemaps list, with `/sitemap.xml` itself."""
    found: dict[str, str] = {"": _SITEMAP_URL} if _has_sitemap(roots) else {}
    for root, module in _sitemap_roots(roots):
        for trail, path in _sitemap_paths(root, module).items():
            found[f"{root.path}:{trail}"] = path
    return found


@register(Tags.urls, NEXT)
def check_robots_disallow(*args, **kwargs) -> list[CheckMessage]:
    """Read every `Disallow` against the sitemap and the noindex pages.

    A prefix over a URL the sitemap lists is `next.W100`, and one over a noindex page
    is `next.W101`, since a crawler kept out never reads the meta tag.
    """
    init_errors, roots = loaded_seo_roots()
    warnings = list(init_errors)
    disallowed = [
        (root.path / ROBOTS_MODULE, prefix)
        for root, module in _robots_roots(roots)
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


def _sources_below(root: CheckedRoot) -> Iterator[Path]:
    """Yield every SEO source file sitting under, but not at, the top of the tree."""
    for dirpath, dirnames, filenames in os.walk(root.path):
        dirnames[:] = sorted(name for name in dirnames if name not in root.skip_names)
        if Path(dirpath) == root.path:
            continue
        for name in (SITEMAP_MODULE, ROBOTS_MODULE, ROBOTS_FILE):
            if name in filenames:
                yield Path(dirpath) / name


@register(Tags.urls, NEXT)
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
    "CheckedRoot",
    "SeoSource",
    "check_robots_disallow",
    "check_robots_file",
    "check_robots_single_source",
    "check_seo_module_attributes",
    "check_seo_module_imports",
    "check_seo_route_collisions",
    "check_seo_routes_at_host_root",
    "check_seo_sources_below_root",
    "check_sitemap_dynamic_routes",
    "check_sitemap_items_trails",
    "check_sitemap_noindex_items",
    "check_sitemap_section_labels",
    "check_sitemap_templates",
    "loaded_seo_roots",
]
