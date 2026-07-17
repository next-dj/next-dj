"""System checks for the URL routing subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.utils.module_loading import import_string

from next.checks.common import (
    errors_for_unknown_keys,
    get_router_manager,
)
from next.conf import next_framework_settings

from .backends import FileRouterBackend, RouterBackend, RouterFactory
from .dispatcher import scan_pages_tree
from .parser import DuplicateURLParameterError, default_url_parser


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from .manager import RouterManager


FILE_ROUTER_BACKEND = "next.urls.FileRouterBackend"

_PAGE_BACKEND_SETTINGS_KEY = "PAGE_BACKENDS"

_FILE_ROUTER_PAGE_CONFIG_KEYS = frozenset(
    {
        "BACKEND",
        "APP_DIRS",
        "DIRS",
        "OPTIONS",
        "PAGES_DIR",
    },
)

_NON_FILE_ROUTER_PAGE_CONFIG_KEYS = frozenset({"BACKEND"})


def _router_backend_path_is_valid(backend_path: str) -> bool:
    """Return True when `backend_path` names a registered or importable backend."""
    if backend_path in RouterFactory._backends:
        return True
    try:
        resolved = import_string(backend_path)
    except ImportError:
        return False
    return isinstance(resolved, type) and issubclass(resolved, RouterBackend)


def _validate_config_structure(
    config: object,
    index: int,
) -> list[CheckMessage]:
    """Validate required keys and types for one `PAGE_BACKENDS` entry."""
    errors: list[CheckMessage] = []

    if not isinstance(config, dict):
        errors.append(
            Error(
                f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}] "
                "must be a dictionary.",
                obj=settings,
                id="next.E002",
            ),
        )
        return errors

    if "BACKEND" not in config:
        errors.append(
            Error(
                f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}] "
                "must specify a BACKEND.",
                obj=settings,
                id="next.E003",
            ),
        )

    return errors


def _validate_file_router_backend_fields(
    config: dict[str, Any],
    index: int,
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
            config,
            allowed=_FILE_ROUTER_PAGE_CONFIG_KEYS,
            prefix=rf_routers,
        ),
    )
    return errors


def _validate_dirs_field(config: dict[str, Any], prefix: str) -> list[CheckMessage]:
    """Validate that `DIRS`, when present, is a list."""
    if "DIRS" in config and not isinstance(config["DIRS"], list):
        return [Error(f"{prefix}.DIRS must be a list.", obj=settings, id="next.E006")]
    return []


def _validate_pages_dir_field(
    config: dict[str, Any],
    prefix: str,
) -> list[CheckMessage]:
    """Validate that `PAGES_DIR` is present and a string."""
    if "PAGES_DIR" not in config:
        return [
            Error(
                f"{prefix} must specify PAGES_DIR when using FileRouterBackend.",
                obj=settings,
                id="next.E024",
            ),
        ]
    if not isinstance(config["PAGES_DIR"], str):
        return [
            Error(
                f"{prefix}.PAGES_DIR must be a string.",
                obj=settings,
                id="next.E027",
            ),
        ]
    return []


def _validate_app_dirs_field(
    config: dict[str, Any],
    prefix: str,
) -> list[CheckMessage]:
    """Validate that `APP_DIRS` is present and a boolean."""
    if "APP_DIRS" not in config:
        return [
            Error(
                f"{prefix} must specify APP_DIRS when using FileRouterBackend.",
                obj=settings,
                id="next.E025",
            ),
        ]
    if not isinstance(config["APP_DIRS"], bool):
        return [
            Error(
                f"{prefix}.APP_DIRS must be a boolean.",
                obj=settings,
                id="next.E005",
            ),
        ]
    return []


def _validate_options_field(
    config: dict[str, Any],
    prefix: str,
) -> list[CheckMessage]:
    """Validate that `OPTIONS` is present, a dict, and only names known keys."""
    if "OPTIONS" not in config:
        return [
            Error(
                f"{prefix} must specify OPTIONS when using FileRouterBackend.",
                obj=settings,
                id="next.E026",
            ),
        ]
    if not isinstance(config["OPTIONS"], dict):
        return [
            Error(
                f"{prefix}.OPTIONS must be a dictionary.", obj=settings, id="next.E006"
            ),
        ]
    opts = config["OPTIONS"]
    errors = _validate_context_processors(opts, prefix)
    errors.extend(_validate_options_unknown_keys(opts, prefix))
    return errors


def _validate_context_processors(
    opts: dict[str, Any],
    prefix: str,
) -> list[CheckMessage]:
    """Validate the `context_processors` option as a list of strings."""
    cp = opts.get("context_processors")
    if cp is not None and not isinstance(cp, list):
        return [
            Error(
                f"{prefix}.OPTIONS['context_processors'] must be a list.",
                obj=settings,
                id="next.E006",
            ),
        ]
    if isinstance(cp, list) and any(not isinstance(item, str) for item in cp):
        return [
            Error(
                f"{prefix}.OPTIONS['context_processors'] must contain only strings.",
                obj=settings,
                id="next.E006",
            ),
        ]
    return []


def _validate_options_unknown_keys(
    opts: dict[str, Any],
    prefix: str,
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
            id="next.E006",
        ),
    ]


def _validate_config_fields(
    config: dict[str, Any],
    index: int,
) -> list[CheckMessage]:
    """Validate specific fields of a single page-backend configuration."""
    errors: list[CheckMessage] = []

    backend = config.get("BACKEND")
    if backend is not None and not _router_backend_path_is_valid(str(backend)):
        errors.append(
            Error(
                f'NEXT_FRAMEWORK["{_PAGE_BACKEND_SETTINGS_KEY}"][{index}] specifies '
                f'unknown backend "{backend}".',
                obj=settings,
                id="next.E004",
            ),
        )

    # Check if backend is FileRouterBackend or a subclass
    is_file_router = False
    if backend == FILE_ROUTER_BACKEND:
        is_file_router = True
    elif backend is not None and isinstance(backend, str):
        try:
            backend_class = import_string(backend)
            is_file_router = isinstance(backend_class, type) and issubclass(
                backend_class, FileRouterBackend
            )
        except (ImportError, AttributeError):
            pass

    if is_file_router:
        errors.extend(_validate_file_router_backend_fields(config, index))
    elif (
        backend is not None
        and isinstance(backend, str)
        and _router_backend_path_is_valid(backend)
    ):
        rf = f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}]"
        errors.extend(
            errors_for_unknown_keys(
                config,
                allowed=_NON_FILE_ROUTER_PAGE_CONFIG_KEYS,
                prefix=rf,
            ),
        )

    return errors


@register(Tags.compatibility)
def check_next_pages_configuration(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate `PAGE_BACKENDS` inside merged `NEXT_FRAMEWORK`."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is not None and not isinstance(raw, dict):
        return [
            Error(
                "NEXT_FRAMEWORK must be a dictionary.",
                obj=settings,
                id="next.E001",
            ),
        ]

    next_pages = next_framework_settings.PAGE_BACKENDS
    if not isinstance(next_pages, list):
        return [
            Error(
                "NEXT_FRAMEWORK['PAGE_BACKENDS'] must be a list of "
                "configuration dictionaries.",
                obj=settings,
                id="next.E001",
            ),
        ]

    if len(next_pages) == 0:
        return [
            Error(
                "NEXT_FRAMEWORK['PAGE_BACKENDS'] must contain at least one "
                "router entry (configure the file router or another backend).",
                obj=settings,
                id="next.E022",
            ),
        ]

    errors: list[CheckMessage] = []
    for i, config in enumerate(next_pages):
        errors.extend(_validate_config_structure(config, i))
        if isinstance(config, dict):
            errors.extend(_validate_config_fields(config, i))

    return errors


@register(Tags.urls)
def check_url_patterns(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Collect patterns from routers and flag duplicate Django path strings."""
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    all_patterns, errors = _collect_all_patterns(router_manager)

    try:
        _check_url_conflicts(all_patterns, errors, warnings)
    except (ValueError, TypeError) as e:
        errors.append(
            Error(
                f"Error checking URL conflicts: {e}",
                obj=settings,
                id="next.E014",
            ),
        )

    return errors + warnings


@register(Tags.urls)
def check_reverse_name_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Fail when two distinct routes collapse to the same reverse URL name."""
    errors: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    # Collection errors surface through check_url_patterns already, so
    # they are dropped here instead of being reported twice.
    all_patterns, _ = _collect_all_patterns(router_manager)

    names: dict[str, dict[str, list[str]]] = {}
    for _pattern, url_path, source in all_patterns:
        full_name = next_framework_settings.URL_NAME_TEMPLATE.format(
            name=default_url_parser.prepare_url_name(url_path),
        )
        names.setdefault(full_name, {}).setdefault(url_path, []).append(source)

    for full_name, routes in names.items():
        # Identical route trails from several trees are an E015 path
        # conflict, so only distinct trails count as a name collision.
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
            ),
        )

    return errors


def _collect_all_patterns(
    router_manager: RouterManager,
) -> tuple[list[tuple[str, str, str]], list[CheckMessage]]:
    """Collect `(pattern, url_path, source)` triples from every router backend."""
    all_patterns: list[tuple[str, str, str]] = []
    errors: list[CheckMessage] = []

    for router in router_manager._backends:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _collect_app_patterns(router, all_patterns, errors)
            _collect_root_patterns(router, all_patterns, errors)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error collecting patterns from router: {e}",
                    obj=settings,
                    id="next.E016",
                ),
            )

    return all_patterns, errors


def _collect_app_patterns(
    router: RouterBackend,
    all_patterns: list[tuple[str, str, str]],
    errors: list[CheckMessage],
) -> None:
    """Append patterns discovered under each app's `pages_dir`."""
    if not hasattr(router, "_get_installed_apps"):
        return

    file_router: FileRouterBackend = router  # type: ignore[assignment]

    for app_name in file_router._get_installed_apps():
        if not hasattr(file_router, "_get_app_pages_path"):
            continue

        pages_path = file_router._get_app_pages_path(app_name)
        if not pages_path:
            continue

        patterns = _collect_url_patterns(
            pages_path,
            f"App '{app_name}'",
            errors,
            skip_dir_names=getattr(router, "_skip_dir_names", frozenset()),
        )
        all_patterns.extend(patterns)


def _collect_root_patterns(
    router: RouterBackend,
    all_patterns: list[tuple[str, str, str]],
    errors: list[CheckMessage],
) -> None:
    """Append patterns from each configured root pages directory."""
    if not hasattr(router, "_get_root_pages_paths"):
        return
    for i, pages_path in enumerate(router._get_root_pages_paths()):
        context = "Root" if i == 0 else f"Root ({pages_path})"
        patterns = _collect_url_patterns(
            pages_path,
            context,
            errors,
            skip_dir_names=getattr(router, "_skip_dir_names", frozenset()),
        )
        all_patterns.extend(patterns)


def _check_url_conflicts(
    all_patterns: list[tuple[str, str, str]],
    errors: list[CheckMessage],
    _warnings: list[CheckMessage],
) -> None:
    """Report an error when the same Django path string comes from multiple sources."""
    pattern_dict: dict[str, list[str]] = {}
    for pattern, _url_path, source in all_patterns:
        if pattern in pattern_dict:
            pattern_dict[pattern].append(source)
        else:
            pattern_dict[pattern] = [source]

    for pattern, sources in pattern_dict.items():
        if len(sources) > 1:
            errors.append(
                Error(
                    f'URL pattern conflict: "{pattern}" is defined in '
                    f"multiple locations: {', '.join(sources)}",
                    obj=settings,
                    id="next.E015",
                ),
            )


def _collect_url_patterns(
    pages_path: Path,
    context: str,
    errors: list[CheckMessage],
    skip_dir_names: Iterable[str] = (),
) -> list[tuple[str, str, str]]:
    """Collect `(pattern, url_path, source)` triples from one pages root.

    Conversion and skip set match the router, so the check sees exactly
    the routes the router registers.
    """
    patterns: list[tuple[str, str, str]] = []

    if not pages_path.exists():
        return patterns

    for url_path, page_file in scan_pages_tree(pages_path, skip_dir_names):
        try:
            django_pattern, _parameters = default_url_parser.parse_url_pattern(
                url_path,
            )
        except DuplicateURLParameterError as exc:
            # Two wildcards with distinct names raise without a name
            # duplicate, so fall back to the name the parser flagged.
            names = default_url_parser.duplicate_parameter_names(url_path) or [
                exc.param_name,
            ]
            errors.append(
                Error(
                    f"URL pattern '{url_path}' has duplicate parameter "
                    f"names: {names}. "
                    "Each parameter must have a unique name.",
                    obj=str(page_file),
                    id="next.E028",
                ),
            )
        except (ValueError, TypeError):
            continue
        else:
            source = f"{context}: {page_file.relative_to(pages_path)}"
            patterns.append((django_pattern, url_path, source))

    return patterns


__all__ = [
    "check_next_pages_configuration",
    "check_reverse_name_collisions",
    "check_url_patterns",
]
