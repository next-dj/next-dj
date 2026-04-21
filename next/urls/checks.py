"""System checks for the URL routing subsystem."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.utils.module_loading import import_string

from next.checks.common import (
    errors_for_unknown_keys,
    get_router_manager,
    iter_scanned_page_pairs,
)
from next.conf import next_framework_settings

from .backends import FileRouterBackend, RouterBackend, RouterFactory
from .parser import URLPatternParser


if TYPE_CHECKING:
    from pathlib import Path


FILE_ROUTER_BACKEND = "next.urls.FileRouterBackend"

_PAGE_BACKEND_SETTINGS_KEY = "DEFAULT_PAGE_BACKENDS"

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
    """Validate required keys and types for one `DEFAULT_PAGE_BACKENDS` entry."""
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


def _validate_file_router_backend_fields(  # noqa: C901, PLR0912
    config: dict[str, Any],
    index: int,
) -> list[CheckMessage]:
    """Validate `DIRS`, `PAGES_DIR`, `APP_DIRS`, `OPTIONS` for the file router."""
    errors: list[CheckMessage] = []
    rf_routers = f"NEXT_FRAMEWORK['{_PAGE_BACKEND_SETTINGS_KEY}'][{index}]"
    if "DIRS" in config and not isinstance(config["DIRS"], list):
        errors.append(
            Error(
                f"{rf_routers}.DIRS must be a list.",
                obj=settings,
                id="next.E006",
            ),
        )

    if "PAGES_DIR" not in config:
        errors.append(
            Error(
                f"{rf_routers} must specify PAGES_DIR when using FileRouterBackend.",
                obj=settings,
                id="next.E024",
            ),
        )
    elif not isinstance(config["PAGES_DIR"], str):
        errors.append(
            Error(
                f"{rf_routers}.PAGES_DIR must be a string.",
                obj=settings,
                id="next.E027",
            ),
        )

    if "APP_DIRS" not in config:
        errors.append(
            Error(
                f"{rf_routers} must specify APP_DIRS when using FileRouterBackend.",
                obj=settings,
                id="next.E025",
            ),
        )
    elif not isinstance(config["APP_DIRS"], bool):
        errors.append(
            Error(
                f"{rf_routers}.APP_DIRS must be a boolean.",
                obj=settings,
                id="next.E005",
            ),
        )

    if "OPTIONS" not in config:
        errors.append(
            Error(
                f"{rf_routers} must specify OPTIONS when using FileRouterBackend.",
                obj=settings,
                id="next.E026",
            ),
        )
    elif not isinstance(config["OPTIONS"], dict):
        errors.append(
            Error(
                f"{rf_routers}.OPTIONS must be a dictionary.",
                obj=settings,
                id="next.E006",
            ),
        )
    else:
        opts = config["OPTIONS"]
        cp = opts.get("context_processors")
        if cp is not None and not isinstance(cp, list):
            errors.append(
                Error(
                    f"{rf_routers}.OPTIONS['context_processors'] must be a list.",
                    obj=settings,
                    id="next.E006",
                ),
            )
        elif isinstance(cp, list):
            for item in cp:
                if not isinstance(item, str):
                    errors.append(
                        Error(
                            f"{rf_routers}.OPTIONS['context_processors'] must contain "
                            "only strings.",
                            obj=settings,
                            id="next.E006",
                        ),
                    )
                    break
        for key in opts:
            if key == "context_processors":
                continue
            errors.append(
                Error(
                    f"{rf_routers}.OPTIONS contains unknown key {key!r}. "
                    "OPTIONS only supports context_processors. "
                    "Use top-level DIRS for extra page roots.",
                    obj=settings,
                    id="next.E006",
                ),
            )
            break

    errors.extend(
        errors_for_unknown_keys(
            config,
            allowed=_FILE_ROUTER_PAGE_CONFIG_KEYS,
            prefix=rf_routers,
        ),
    )
    return errors


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
    """Validate `DEFAULT_PAGE_BACKENDS` inside merged `NEXT_FRAMEWORK`."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is not None and not isinstance(raw, dict):
        return [
            Error(
                "NEXT_FRAMEWORK must be a dictionary.",
                obj=settings,
                id="next.E001",
            ),
        ]

    next_pages = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(next_pages, list):
        return [
            Error(
                "NEXT_FRAMEWORK['DEFAULT_PAGE_BACKENDS'] must be a list of "
                "configuration dictionaries.",
                obj=settings,
                id="next.E001",
            ),
        ]

    if len(next_pages) == 0:
        return [
            Error(
                "NEXT_FRAMEWORK['DEFAULT_PAGE_BACKENDS'] must contain at least one "
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


def _get_duplicate_parameters(url_path: str, parser: URLPatternParser) -> list[str]:
    """Return parameter names that appear more than once in bracket segments."""
    param_matches = parser._param_pattern.findall(url_path)
    param_names = []
    for param_str in param_matches:
        param_name, _ = parser._parse_param_name_and_type(param_str)
        param_names.append(param_name)

    if len(param_names) == len(set(param_names)):
        return []

    return [name for name in set(param_names) if param_names.count(name) > 1]


@register(Tags.urls)
def check_duplicate_url_parameters(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Fail when the same bracket parameter name is repeated in one route."""
    errors: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    parser = URLPatternParser()

    for router in router_manager._backends:
        for url_path, page_path in iter_scanned_page_pairs(router):
            if not page_path.exists():
                continue

            try:
                parser.parse_url_pattern(url_path)
                duplicates = _get_duplicate_parameters(url_path, parser)

                if duplicates:
                    errors.append(
                        Error(
                            f"URL pattern '{url_path}' has duplicate parameter "
                            f"names: {duplicates}. "
                            "Each parameter must have a unique name.",
                            obj=str(page_path),
                            id="next.E028",
                        ),
                    )
            except (ValueError, TypeError, AttributeError):
                continue

    return errors


@register(Tags.urls)
def check_url_patterns(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Collect patterns from routers and flag duplicate Django path strings."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    all_patterns: list[tuple[str, str]] = []

    for router in router_manager._backends:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _collect_app_patterns(router, all_patterns)
            _collect_root_patterns(router, all_patterns)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error collecting patterns from router: {e}",
                    obj=settings,
                    id="next.E016",
                ),
            )

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


def _collect_app_patterns(
    router: RouterBackend,
    all_patterns: list[tuple[str, str]],
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

        patterns = _collect_url_patterns(pages_path, f"App '{app_name}'")
        all_patterns.extend(patterns)


def _collect_root_patterns(
    router: RouterBackend,
    all_patterns: list[tuple[str, str]],
) -> None:
    """Append patterns from each configured root pages directory."""
    if not hasattr(router, "_get_root_pages_paths"):
        return
    for i, pages_path in enumerate(router._get_root_pages_paths()):
        context = "Root" if i == 0 else f"Root ({pages_path})"
        patterns = _collect_url_patterns(pages_path, context)
        all_patterns.extend(patterns)


def _check_url_conflicts(
    all_patterns: list[tuple[str, str]],
    errors: list[CheckMessage],
    _warnings: list[CheckMessage],
) -> None:
    """Report an error when the same Django path string comes from multiple sources."""
    pattern_dict: dict[str, list[str]] = {}
    for pattern, source in all_patterns:
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


def _collect_url_patterns(pages_path: Path, context: str) -> list[tuple[str, str]]:
    """Collect URL patterns from a pages directory for conflict comparison."""
    patterns: list[tuple[str, str]] = []

    if not pages_path.exists():
        return patterns

    for page_file in pages_path.rglob("page.py"):
        try:
            relative_path = page_file.relative_to(pages_path)
            url_path = str(relative_path.parent)

            if django_pattern := _convert_to_django_pattern(url_path):
                patterns.append((django_pattern, f"{context}: {relative_path}"))

        except (OSError, ValueError):
            continue

    return patterns


def _convert_to_django_pattern(url_path: str) -> str | None:
    """Convert bracket syntax to `<str:>` / `<path:>` for conflict comparison."""
    if not url_path:
        return ""

    args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")
    url_path = args_pattern.sub(r"<path:\1>", url_path)

    param_pattern = re.compile(r"\[([^\[\]]+)\]")
    return param_pattern.sub(r"<str:\1>", url_path)


__all__ = [
    "check_duplicate_url_parameters",
    "check_next_pages_configuration",
    "check_url_patterns",
]
