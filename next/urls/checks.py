"""System checks for the URL routing subsystem."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.urls import NoReverseMatch, Resolver404, resolve

from next.checks import NEXT, SEO
from next.checks.common import (
    WALK_HINT,
    discover_page_registrations,
    errors_for_unknown_keys,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.conf import import_class_cached, next_framework_settings
from next.csrf import csrf_url, csrf_view
from next.forms.uid import URL_NAME_FORM_ACTION, reverse_form_action
from next.pages.access import routed_pages
from next.ports import form_routes_slot
from next.utils import UNMOUNTED, PageRoot, is_route_group

from .backends import FILE_ROUTER_CONFIG_KEYS, FileRouterBackend, RouterBackend
from .dispatcher import scan_pages_tree
from .errors import (
    DuplicateURLParameterError,
    ReservedURLParameterError,
    URLParameterError,
)
from .parser import RESERVED_ROUTE_NAMES, default_url_parser
from .urlconf import NO_MOUNTS, UrlconfMounts, outer_kwargs, tree_route, urlconf_mounts


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django.urls import ResolverMatch

    from .manager import RouterManager


_PAGE_BACKEND_SETTINGS_KEY = "PAGE_BACKENDS"

_FILE_ROUTER_PAGE_CONFIG_KEYS = frozenset({"BACKEND", *FILE_ROUTER_CONFIG_KEYS})

_NON_FILE_ROUTER_PAGE_CONFIG_KEYS = frozenset({"BACKEND"})


class _CollectedPattern(NamedTuple):
    """One route as the URL checks compare it, and whether its page sits in a group."""

    pattern: str
    url_path: str
    source: str
    parameters: frozenset[str]
    namespace: str
    grouped: bool


_GROUP_CLASH_HINT = (
    "A route group leaves the URL, so two groups, or a group page and the page of "
    "its parent, serve one URL. Keep one page there, or move the other under a "
    "segment of its own."
)

_RESERVED_SEGMENT_ID = "next.E217"

_RESERVED_SEGMENT_HINT = (
    "Rename the bracket directory. The framework passes these names to page, form "
    f"and component callables itself: {', '.join(sorted(RESERVED_ROUTE_NAMES))}."
)

type _TreeKey = tuple[Path, str, str]
"""A mounted tree by its path, the route it is included under and its namespace."""


def _router_class(backend_path: str) -> type[RouterBackend] | None:
    """Return the router class `backend_path` names, or None when it names none.

    One read answers both whether the entry is valid and whether it is a file router.
    """
    try:
        resolved = import_class_cached(backend_path)
    except ImportError:
        return None
    if isinstance(resolved, type) and issubclass(resolved, RouterBackend):
        return resolved
    return None


def _validate_config_structure(config: object, index: int) -> list[CheckMessage]:
    """Validate required keys and types for one `PAGE_BACKENDS` entry."""
    errors: list[CheckMessage] = []

    if not isinstance(config, dict):
        errors.append(
            Error(
                f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}] "
                "must be a dictionary.",
                obj=settings,
                id="next.E002",
            )
        )
        return errors

    if "BACKEND" not in config:
        errors.append(
            Error(
                f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}] "
                "must specify a BACKEND.",
                obj=settings,
                id="next.E003",
            )
        )

    return errors


def _validate_file_router_backend_fields(
    config: dict[str, Any], index: int
) -> list[CheckMessage]:
    """Validate `DIRS`, `PAGES_DIR`, `APP_DIRS`, `OPTIONS` for the file router."""
    rf_routers = f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}]"
    errors: list[CheckMessage] = []
    errors.extend(_validate_dirs_field(config, rf_routers))
    errors.extend(_validate_pages_dir_field(config, rf_routers))
    errors.extend(_validate_app_dirs_field(config, rf_routers))
    errors.extend(_validate_options_field(config, rf_routers))
    errors.extend(
        errors_for_unknown_keys(
            config, allowed=_FILE_ROUTER_PAGE_CONFIG_KEYS, prefix=rf_routers
        )
    )
    return errors


def _validate_dirs_field(config: dict[str, Any], prefix: str) -> list[CheckMessage]:
    """Validate that `DIRS`, when present, is a list of paths and segment names.

    The router drops an entry it cannot read, so only this check names the cause.
    """
    if "DIRS" not in config:
        return []
    dirs = config["DIRS"]
    if not isinstance(dirs, list):
        return [Error(f"{prefix}.DIRS must be a list.", obj=settings, id="next.E006")]
    bad = [item for item in dirs if not isinstance(item, str | os.PathLike | None)]
    if bad:
        msg = f"{prefix}.DIRS holds {bad!r}, neither a path nor a segment name."
        return [Error(msg, obj=settings, id="next.E006")]
    return []


def _validate_pages_dir_field(
    config: dict[str, Any], prefix: str
) -> list[CheckMessage]:
    """Validate that `PAGES_DIR` is present and a string."""
    if "PAGES_DIR" not in config:
        return [
            Error(
                f"{prefix} must specify PAGES_DIR when using FileRouterBackend.",
                obj=settings,
                id="next.E024",
            )
        ]
    if not isinstance(config["PAGES_DIR"], str):
        return [
            Error(f"{prefix}.PAGES_DIR must be a string.", obj=settings, id="next.E027")
        ]
    return []


def _validate_app_dirs_field(config: dict[str, Any], prefix: str) -> list[CheckMessage]:
    """Validate that `APP_DIRS` is present and a boolean."""
    if "APP_DIRS" not in config:
        return [
            Error(
                f"{prefix} must specify APP_DIRS when using FileRouterBackend.",
                obj=settings,
                id="next.E025",
            )
        ]
    if not isinstance(config["APP_DIRS"], bool):
        return [
            Error(f"{prefix}.APP_DIRS must be a boolean.", obj=settings, id="next.E005")
        ]
    return []


def _validate_options_field(config: dict[str, Any], prefix: str) -> list[CheckMessage]:
    """Validate that `OPTIONS`, when present, is a dict naming only known keys."""
    if "OPTIONS" not in config:
        return []
    if not isinstance(config["OPTIONS"], dict):
        return [
            Error(
                f"{prefix}.OPTIONS must be a dictionary.", obj=settings, id="next.E094"
            )
        ]
    opts = config["OPTIONS"]
    errors = _validate_context_processors(opts, prefix)
    errors.extend(_validate_options_unknown_keys(opts, prefix))
    return errors


def _validate_context_processors(
    opts: dict[str, Any], prefix: str
) -> list[CheckMessage]:
    """Validate the `context_processors` option as a list of strings."""
    cp = opts.get("context_processors")
    if cp is not None and not isinstance(cp, list):
        return [
            Error(
                f"{prefix}.OPTIONS['context_processors'] must be a list.",
                obj=settings,
                id="next.E095",
            )
        ]
    if isinstance(cp, list) and any(not isinstance(item, str) for item in cp):
        return [
            Error(
                f"{prefix}.OPTIONS['context_processors'] must contain only strings.",
                obj=settings,
                id="next.E096",
            )
        ]
    return []


def _validate_options_unknown_keys(
    opts: dict[str, Any], prefix: str
) -> list[CheckMessage]:
    """Report the first `OPTIONS` key that is not `context_processors`."""
    unknown = next((key for key in opts if key != "context_processors"), None)
    if unknown is None:
        return []
    return [
        Error(
            f"{prefix}.OPTIONS contains unknown key {unknown!r}. "
            "OPTIONS only supports context_processors. "
            "Use top-level DIRS for extra page roots.",
            obj=settings,
            id="next.E097",
        )
    ]


def _validate_config_fields(config: dict[str, Any], index: int) -> list[CheckMessage]:
    """Validate specific fields of a single page-backend configuration."""
    errors: list[CheckMessage] = []

    backend = config.get("BACKEND")
    if backend is None:
        return errors

    router_class = _router_class(str(backend))
    if router_class is None:
        errors.append(
            Error(
                f'NEXT_FRAMEWORK["{_PAGE_BACKEND_SETTINGS_KEY}"][{index}] specifies '
                f'unknown backend "{backend}".',
                obj=settings,
                id="next.E004",
            )
        )
        return errors

    if issubclass(router_class, FileRouterBackend):
        errors.extend(_validate_file_router_backend_fields(config, index))
    else:
        rf = f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}]"
        errors.extend(
            errors_for_unknown_keys(
                config, allowed=_NON_FILE_ROUTER_PAGE_CONFIG_KEYS, prefix=rf
            )
        )

    return errors


@register(NEXT)
def check_next_pages_configuration(*args, **kwargs) -> list[CheckMessage]:
    """Validate `PAGE_BACKENDS` inside merged `NEXT_FRAMEWORK`.

    A non-dict `NEXT_FRAMEWORK` is `next.E077` elsewhere, so the defaults are read here.
    """
    next_pages = next_framework_settings.PAGE_BACKENDS
    if not isinstance(next_pages, list):
        return [
            Error(
                "NEXT_FRAMEWORK['PAGE_BACKENDS'] must be a list of "
                "configuration dictionaries.",
                obj=settings,
                id="next.E081",
            )
        ]

    if len(next_pages) == 0:
        return [
            Error(
                "NEXT_FRAMEWORK['PAGE_BACKENDS'] must contain at least one "
                "router entry (configure the file router or another backend).",
                obj=settings,
                id="next.E022",
            )
        ]

    errors: list[CheckMessage] = []
    for i, config in enumerate(next_pages):
        errors.extend(_validate_config_structure(config, i))
        if isinstance(config, dict):
            errors.extend(_validate_config_fields(config, i))

    return errors


@register(Tags.urls, Tags.templates, NEXT, SEO)
def check_router_manager(*args, **kwargs) -> list[CheckMessage]:
    """Report a router that fails to initialise (`next.E007`), once per check run.

    Router-walking checks skip the failure silently, so this carries every tag they use.
    """
    _router_manager, init_errors = get_router_manager()
    return init_errors


@register(Tags.urls, NEXT)
def check_url_patterns(*args, **kwargs) -> list[CheckMessage]:
    """Collect patterns from routers and flag duplicate Django path strings."""
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []

    all_patterns, collected = _collect_all_patterns(router_manager)
    errors = [error for error in collected if error.id != _RESERVED_SEGMENT_ID]

    try:
        _check_url_conflicts(all_patterns, errors)
    except (ValueError, TypeError) as e:
        errors.append(
            Error(
                f"The URL patterns the routers produced could not be compared: {e}",
                hint=(
                    "A router answered a pattern that is not a Django URL pattern. "
                    "Check the custom router named in PAGE_BACKENDS."
                ),
                obj=settings,
                id="next.E014",
            )
        )

    return errors


@register(Tags.urls, NEXT)
def check_reserved_url_parameters(*args, **kwargs) -> list[CheckMessage]:
    """Report a bracket segment named after an argument the framework passes itself."""
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []
    _patterns, collected = _collect_all_patterns(router_manager)
    return [error for error in collected if error.id == _RESERVED_SEGMENT_ID]


@register(Tags.urls, NEXT)
def check_reverse_name_collisions(*args, **kwargs) -> list[CheckMessage]:
    """Fail when two distinct routes collapse to the same reverse URL name."""
    errors: list[CheckMessage] = []

    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []

    # Collection errors already surface through check_url_patterns.
    all_patterns, _ = _collect_all_patterns(router_manager)

    # reverse() picks by arguments, so a collision needs name and parameters both.
    grouped: dict[tuple[str, str, frozenset[str]], dict[str, list[str]]] = {}
    for _pattern, url_path, source, parameters, namespace, _grouped in all_patterns:
        full_name = next_framework_settings.URL_NAME_TEMPLATE.format(
            name=default_url_parser.prepare_url_name(url_path)
        )
        grouped.setdefault((namespace, full_name, parameters), {}).setdefault(
            url_path, []
        ).append(source)

    for (_namespace, full_name, _signature), routes in grouped.items():
        # One trail from several trees is an E015 path conflict, not this.
        if len(routes) == 1:
            continue
        sources = ", ".join(
            source for route_sources in routes.values() for source in route_sources
        )
        errors.append(
            Error(
                f'Reverse URL name collision: "{full_name}" is produced by '
                f"multiple routes: {sources}. reverse() resolves only one of "
                "them. Rename the conflicting directories.",
                obj=settings,
                id="next.E039",
            )
        )

    return errors


class _CollectedPatternsMemo:
    """One collection walk shared by the two URL checks of a run.

    The manager is held rather than its `id`, so a later one can never alias it.
    """

    def __init__(self) -> None:
        """Start with no walk on record."""
        self.router_manager: RouterManager | None = None
        self.patterns: list[_CollectedPattern] = []
        self.errors: list[CheckMessage] = []


_collected_patterns = _CollectedPatternsMemo()


_PROBE_UID = "probe"
"""A form action uid any `<str:uid>` route accepts, so the address resolves as one."""


def _framework_routes() -> tuple[tuple[str, Callable[[], str], frozenset[Any]], ...]:
    """Return the label, reverse call and serving views of each framework endpoint.

    A `page.py` registers its actions as it runs, so the page tree is discovered first.
    """
    discover_page_registrations()
    action_views = frozenset(
        pattern.callback
        for pattern in form_routes_slot.get()
        if pattern.name == URL_NAME_FORM_ACTION
    )
    return (
        ("CSRF token endpoint", csrf_url, frozenset({csrf_view})),
        ("form action endpoint", lambda: reverse_form_action(_PROBE_UID), action_views),
    )


def _view_path(func: Callable[..., Any]) -> str:
    """Return the dotted path of a view, the class of a class-based or callable one."""
    view = getattr(func, "view_class", func)
    if not hasattr(view, "__qualname__"):
        view = type(view)
    return f"{view.__module__}.{view.__qualname__}"


def _answered_by(match: ResolverMatch) -> str:
    """Describe the view of `match`, by its page file when a page answers."""
    page_path = getattr(match.func, "next_page_path", None)
    if page_path is not None:
        return f"the page {page_path} (route {match.route!r})"
    return f"{_view_path(match.func)} (route {match.route!r})"


@register(Tags.urls, NEXT)
def check_framework_routes_reachable(*args, **kwargs) -> list[CheckMessage]:
    """Report a framework endpoint that a view other than its own answers (`next.E149`).

    It compares views, not URL names, so a project view reusing the name fails.
    """
    errors: list[CheckMessage] = []
    for label, address, views in _framework_routes():
        try:
            url = address()
        except NoReverseMatch:
            continue
        try:
            match: ResolverMatch | None = resolve(url)
        except Resolver404:
            match = None
        if match is not None and match.func in views:
            continue
        answered = "nothing" if match is None else _answered_by(match)
        errors.append(
            Error(
                f"The {label} reverses to {url}, but ROOT_URLCONF resolves that "
                f"address to {answered}, so the runtime never reaches the endpoint.",
                hint=(
                    "List path('', include('next.urls')) above that pattern in "
                    "ROOT_URLCONF, or move the page off that route."
                ),
                obj=settings,
                id="next.E149",
            )
        )
    return errors


def reset_collected_patterns_cache(**kwargs) -> None:
    """Drop memoised collected patterns so the next check run recollects."""
    _collected_patterns.router_manager = None
    _collected_patterns.patterns = []
    _collected_patterns.errors = []


def _collect_all_patterns(
    router_manager: RouterManager,
) -> tuple[list[_CollectedPattern], list[CheckMessage]]:
    """Return per-run memoised patterns as fresh lists callers may mutate."""
    if _collected_patterns.router_manager is not router_manager:
        patterns, errors = _collect_all_patterns_uncached(router_manager)
        _collected_patterns.patterns = patterns
        _collected_patterns.errors = errors
        _collected_patterns.router_manager = router_manager
    return list(_collected_patterns.patterns), list(_collected_patterns.errors)


def _collect_all_patterns_uncached(
    router_manager: RouterManager,
) -> tuple[list[_CollectedPattern], list[CheckMessage]]:
    """Collect one `_CollectedPattern` per route from every router backend."""
    all_patterns: list[_CollectedPattern] = []
    errors: list[CheckMessage] = []
    seen: set[_TreeKey] = set()

    for router in router_manager.backends:
        try:
            _collect_router_patterns(router, all_patterns, errors, seen)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"{type(router).__name__} failed to list its URL patterns: {e}",
                    hint=WALK_HINT,
                    obj=settings,
                    id="next.E016",
                )
            )

    return all_patterns, errors


def _collect_router_patterns(
    router: RouterBackend,
    all_patterns: list[_CollectedPattern],
    errors: list[CheckMessage],
    seen: set[_TreeKey],
) -> None:
    """Append patterns from every page tree `router` reports.

    Each file router labels a mounted tree its own way, so its route keys it once.
    """
    skip_dir_names = page_tree_skip_names(router)
    mounts = urlconf_mounts()
    for root in get_page_roots(router):
        if root.prefix is not None:
            key = (root.path, root.prefix, root.namespace)
            if key in seen:
                continue
            seen.add(key)
        all_patterns.extend(
            _collect_url_patterns(
                root.path,
                root.label,
                errors,
                skip_dir_names=skip_dir_names,
                root=root,
                mounts=mounts,
            )
        )


def _check_url_conflicts(
    all_patterns: list[_CollectedPattern], errors: list[CheckMessage]
) -> None:
    """Report an error when the same Django path string comes from multiple sources."""
    pattern_dict: dict[str, list[_CollectedPattern]] = {}
    for collected in all_patterns:
        pattern_dict.setdefault(collected.pattern, []).append(collected)

    for pattern, clashing in pattern_dict.items():
        if len(clashing) > 1:
            sources = ", ".join(collected.source for collected in clashing)
            grouped = any(collected.grouped for collected in clashing)
            errors.append(
                Error(
                    f'URL pattern conflict: "{pattern}" is defined in '
                    f"multiple locations: {sources}",
                    hint=_GROUP_CLASH_HINT if grouped else None,
                    obj=settings,
                    id="next.E015",
                )
            )


def _collect_url_patterns(
    pages_path: Path,
    context: str,
    errors: list[CheckMessage],
    skip_dir_names: Iterable[str] = (),
    *,
    root: PageRoot = UNMOUNTED,
    mounts: UrlconfMounts = NO_MOUNTS,
) -> list[_CollectedPattern]:
    """Collect one `_CollectedPattern` per route from one pages root.

    It matches the router walk and keys each route from the URLconf root.
    """
    patterns: list[_CollectedPattern] = []

    if not pages_path.exists():
        return patterns

    head = tree_route(root, mounts.include_point or "")
    prefix_names = outer_kwargs(root, mounts)
    for url_path, page_file in routed_pages(
        scan_pages_tree(pages_path, skip_dir_names)
    ):
        try:
            django_pattern, parameters = default_url_parser.parse_url_pattern(url_path)
        except URLParameterError as exc:
            errors.append(_parameter_refusal_error(exc, url_path, page_file))
            continue
        clash = sorted(prefix_names.intersection(parameters))
        if clash:
            errors.append(_prefix_clash_error(head, clash, page_file))
            continue
        relative = page_file.relative_to(pages_path)
        patterns.append(
            _CollectedPattern(
                f"{head}{django_pattern}",
                url_path,
                f"{context}: {relative}",
                frozenset(parameters),
                root.namespace,
                any(is_route_group(part) for part in relative.parts),
            )
        )

    return patterns


def _prefix_clash_error(head: str, names: list[str], page_file: Path) -> Error:
    """Report a page parameter that the include over its tree already passes."""
    return Error(
        f"The page {page_file} captures {names}, which the include {head!r} of its "
        "tree passes already, as a captured or an extra kwarg, so one value would "
        "overwrite the other.",
        hint="Rename the bracket directory, or the kwarg the include passes.",
        obj=str(page_file),
        id="next.E152",
    )


def _parameter_refusal_error(
    exc: URLParameterError, url_path: str, page_file: Path
) -> Error:
    """Report one parser refusal under the code its own subclass owns."""
    if isinstance(exc, DuplicateURLParameterError):
        # Two wildcards with distinct names raise no duplicate, so use the flagged name.
        names = default_url_parser.duplicate_parameter_names(url_path) or [
            exc.param_name
        ]
        return Error(
            f"URL pattern '{url_path}' has duplicate parameter names: {names}. "
            "Each parameter must have a unique name.",
            obj=str(page_file),
            id="next.E028",
        )
    if isinstance(exc, ReservedURLParameterError):
        return Error(
            str(exc),
            hint=_RESERVED_SEGMENT_HINT,
            obj=str(page_file),
            id=_RESERVED_SEGMENT_ID,
        )
    return Error(str(exc), obj=str(page_file), id="next.E082")


__all__ = [
    "check_next_pages_configuration",
    "check_reserved_url_parameters",
    "check_reverse_name_collisions",
    "check_url_patterns",
    "reset_collected_patterns_cache",
]
