from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.checks import WARNING, CheckMessage, Error, Tags, register

from .urls import RouterFactory, RouterManager


@register(Tags.compatibility)
def check_next_pages_configuration(
    app_configs: Any, **kwargs: Any
) -> list[CheckMessage]:
    """Check NEXT_PAGES configuration for errors."""
    errors: list[CheckMessage] = []

    # check if NEXT_PAGES is defined
    if (next_pages := getattr(settings, "NEXT_PAGES", None)) is None:
        return []  # no configuration means default will be used

    if not isinstance(next_pages, list):
        errors.append(
            Error(
                "NEXT_PAGES must be a list of configuration dictionaries.",
                obj=settings,
                id="next.E001",
            )
        )
        return errors

    # validate each configuration
    for i, config in enumerate(next_pages):
        if not isinstance(config, dict):
            errors.append(
                Error(
                    f"NEXT_PAGES[{i}] must be a dictionary.",
                    obj=settings,
                    id="next.E002",
                )
            )
            continue

        # check required fields
        if "BACKEND" not in config:
            errors.append(
                Error(
                    f"NEXT_PAGES[{i}] must specify a BACKEND.",
                    obj=settings,
                    id="next.E003",
                )
            )

        # check backend validity
        if (
            backend := config.get("BACKEND")
        ) and backend not in RouterFactory._backends:
            errors.append(
                Error(
                    f'NEXT_PAGES[{i}] specifies unknown backend "{backend}".',
                    obj=settings,
                    id="next.E004",
                )
            )

        # check APP_DIRS
        if (app_dirs := config.get("APP_DIRS", True)) is not isinstance(app_dirs, bool):
            errors.append(
                Error(
                    f"NEXT_PAGES[{i}].APP_DIRS must be a boolean.",
                    obj=settings,
                    id="next.E005",
                )
            )

        # check OPTIONS
        if (options := config.get("OPTIONS", {})) is not isinstance(options, dict):
            errors.append(
                Error(
                    f"NEXT_PAGES[{i}].OPTIONS must be a dictionary.",
                    obj=settings,
                    id="next.E006",
                )
            )

    return errors


@register(Tags.compatibility)
def check_pages_structure(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Check pages directory structure for potential issues."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    try:
        router_manager = RouterManager()
        router_manager.reload_config()

        for router in router_manager:
            # type check to ensure we're working with FileRouterBackend
            if hasattr(router, "app_dirs") and router.app_dirs:
                # check app pages
                if hasattr(router, "_get_installed_apps"):
                    for app_name in router._get_installed_apps():
                        if hasattr(router, "_get_app_pages_path"):
                            if (
                                pages_path := router._get_app_pages_path(app_name)
                            ) and hasattr(router, "pages_dir_name"):
                                app_errors, app_warnings = _check_pages_directory(
                                    pages_path,
                                    f"App '{app_name}'",
                                    router.pages_dir_name,
                                )
                                errors.extend(app_errors)
                                warnings.extend(app_warnings)
            else:
                # check root pages
                if hasattr(router, "_get_root_pages_path"):
                    if (pages_path := router._get_root_pages_path()) and hasattr(
                        router, "pages_dir_name"
                    ):
                        root_errors, root_warnings = _check_pages_directory(
                            pages_path, "Root", router.pages_dir_name
                        )
                        errors.extend(root_errors)
                        warnings.extend(root_warnings)

    except Exception as e:
        errors.append(
            Error(
                f"Error checking pages structure: {e}",
                obj=settings,
                id="next.E007",
            )
        )

    return errors + warnings


def _check_pages_directory(
    pages_path: Path, context: str, dir_name: str
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check a specific pages directory for issues."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    if not pages_path.exists():
        return errors, warnings

    # check for invalid directory names
    for item in pages_path.rglob("*"):
        if item.is_dir():
            dir_name_str = item.name

            # check for invalid parameter syntax
            if dir_name_str.startswith("[") and dir_name_str.endswith("]"):
                if not _is_valid_parameter_syntax(dir_name_str):
                    errors.append(
                        Error(
                            f'{context} pages: Invalid parameter syntax "{dir_name_str}" in {item.relative_to(pages_path)}. '
                            f"Use [param] or [type:param] format.",
                            obj=settings,
                            id="next.E008",
                        )
                    )

            # check for invalid args syntax
            elif dir_name_str.startswith("[[") and dir_name_str.endswith("]]"):
                if not _is_valid_args_syntax(dir_name_str):
                    errors.append(
                        Error(
                            f'{context} pages: Invalid args syntax "{dir_name_str}" in {item.relative_to(pages_path)}. '
                            f"Use [[args]] format.",
                            obj=settings,
                            id="next.E009",
                        )
                    )

            # check for incomplete parameter/args syntax
            elif dir_name_str.startswith("["):
                # incomplete args syntax
                errors.append(
                    Error(
                        f'{context} pages: Incomplete args syntax "{dir_name_str}" in {item.relative_to(pages_path)}. '
                        f"Use [[args]] format.",
                        obj=settings,
                        id="next.E009",
                    )
                )
            elif dir_name_str.startswith("["):
                # incomplete parameter syntax
                errors.append(
                    Error(
                        f'{context} pages: Incomplete parameter syntax "{dir_name_str}" in {item.relative_to(pages_path)}. '
                        f"Use [param] or [type:param] format.",
                        obj=settings,
                        id="next.E008",
                    )
                )

    # check for missing page.py files in parameter directories
    for item in pages_path.rglob("*"):
        if item.is_dir():
            dir_name_str = item.name
            # check all parameter directories for missing page.py
            if (dir_name_str.startswith("[") and dir_name_str.endswith("]")) or (
                dir_name_str.startswith("[[") and dir_name_str.endswith("]]")
            ):
                page_file = item / "page.py"
                if not page_file.exists():
                    errors.append(
                        Error(
                            f'{context} pages: Parameter directory "{item.relative_to(pages_path)}" is missing page.py file.',
                            obj=settings,
                            id="next.E010",
                        )
                    )

    # check for page.py files in non-parameter directories
    for item in pages_path.rglob("page.py"):
        parent_dir = item.parent
        if parent_dir.name.startswith("[") or parent_dir.name.startswith("[["):
            continue  # this is expected

        # check if parent directory has other files (potential issue)
        if [f for f in parent_dir.iterdir() if f.name != "page.py"]:
            warnings.append(
                CheckMessage(
                    WARNING,
                    f'{context} pages: Directory "{parent_dir.relative_to(pages_path)}" contains page.py and other files. '
                    f"Consider organizing your pages better.",
                    obj=settings,
                    id="next.W001",
                )
            )

    return errors, warnings


def _is_valid_parameter_syntax(param_str: str) -> bool:
    """Check if parameter syntax is valid."""
    if not (param_str.startswith("[") and param_str.endswith("]")):
        return False

    content = param_str[1:-1]
    if ":" in content:
        parts = content.split(":", 1)
        if len(parts) != 2:
            return False
        type_name, param_name = parts
        # check that there are no additional colons
        if ":" in param_name:
            return False
        return bool(type_name.strip() and param_name.strip())
    else:
        return bool(content.strip())


def _is_valid_args_syntax(args_str: str) -> bool:
    """Check if args syntax is valid."""
    if not (args_str.startswith("[[") and args_str.endswith("]]")):
        return False

    content = args_str[2:-2]
    return bool(content.strip())


@register(Tags.compatibility)
def check_page_functions(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Check page.py files for valid render functions."""
    errors: list[CheckMessage] = []

    try:
        router_manager = RouterManager()
        router_manager.reload_config()

        for router in router_manager:
            if hasattr(router, "app_dirs") and router.app_dirs:
                # check app pages
                if hasattr(router, "_get_installed_apps"):
                    for app_name in router._get_installed_apps():
                        if hasattr(router, "_get_app_pages_path"):
                            if pages_path := router._get_app_pages_path(app_name):
                                app_errors = _check_page_functions_in_directory(
                                    pages_path, f"App '{app_name}'"
                                )
                                errors.extend(app_errors)
            else:
                # check root pages
                if hasattr(router, "_get_root_pages_path"):
                    if pages_path := router._get_root_pages_path():
                        root_errors = _check_page_functions_in_directory(
                            pages_path, "Root"
                        )
                        errors.extend(root_errors)

    except Exception as e:
        errors.append(
            Error(
                f"Error checking page functions: {e}",
                obj=settings,
                id="next.E011",
            )
        )

    return errors


def _check_page_functions_in_directory(
    pages_path: Path, context: str
) -> list[CheckMessage]:
    """Check page.py files in a directory for valid render functions."""
    errors: list[CheckMessage] = []

    if not pages_path.exists():
        return errors

    for page_file in pages_path.rglob("page.py"):
        try:
            if (render_func := _load_render_function(page_file)) is None:
                errors.append(
                    Error(
                        f"{context} pages: {page_file.relative_to(pages_path)} is missing a valid render function.",
                        obj=settings,
                        id="next.E012",
                    )
                )
            elif not callable(render_func):
                errors.append(
                    Error(
                        f"{context} pages: {page_file.relative_to(pages_path)} render attribute is not callable.",
                        obj=settings,
                        id="next.E013",
                    )
                )
        except Exception as e:
            errors.append(
                Error(
                    f"{context} pages: Error loading {page_file.relative_to(pages_path)}: {e}",
                    obj=settings,
                    id="next.E014",
                )
            )

    return errors


def _load_render_function(file_path: Path) -> Any | None:
    """Load render function from page.py file."""
    try:
        import importlib.util

        if (
            spec := importlib.util.spec_from_file_location("page_module", file_path)
        ) is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        return getattr(module, "render", None)

    except Exception:
        return None


@register(Tags.compatibility)
def check_url_patterns(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Check for potential URL pattern conflicts."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    try:
        router_manager = RouterManager()
        router_manager.reload_config()

        # collect all URL patterns
        all_patterns: list[tuple[str, str]] = []  # (pattern, source)

        for router in router_manager:
            if hasattr(router, "app_dirs") and router.app_dirs:
                if hasattr(router, "_get_installed_apps"):
                    for app_name in router._get_installed_apps():
                        if hasattr(router, "_get_app_pages_path"):
                            if pages_path := router._get_app_pages_path(app_name):
                                patterns = _collect_url_patterns(
                                    pages_path, f"App '{app_name}'"
                                )
                                all_patterns.extend(patterns)
            else:
                if hasattr(router, "_get_root_pages_path"):
                    if pages_path := router._get_root_pages_path():
                        patterns = _collect_url_patterns(pages_path, "Root")
                        all_patterns.extend(patterns)

        # check for conflicts
        pattern_dict: dict[str, list[str]] = {}
        for pattern, source in all_patterns:
            if pattern in pattern_dict:
                pattern_dict[pattern].append(source)
            else:
                pattern_dict[pattern] = [source]

        # report conflicts
        for pattern, sources in pattern_dict.items():
            if len(sources) > 1:
                errors.append(
                    Error(
                        f'URL pattern conflict: "{pattern}" is defined in multiple locations: {", ".join(sources)}',
                        obj=settings,
                        id="next.E015",
                    )
                )

    except Exception as e:
        errors.append(
            Error(
                f"Error checking URL patterns: {e}",
                obj=settings,
                id="next.E016",
            )
        )

    return errors + warnings


def _collect_url_patterns(pages_path: Path, context: str) -> list[tuple[str, str]]:
    """Collect URL patterns from a pages directory."""
    patterns: list[tuple[str, str]] = []

    if not pages_path.exists():
        return patterns

    for page_file in pages_path.rglob("page.py"):
        try:
            # convert file path to URL pattern
            relative_path = page_file.relative_to(pages_path)
            url_path = str(relative_path.parent)

            # convert to Django pattern
            if django_pattern := _convert_to_django_pattern(url_path):
                patterns.append((django_pattern, f"{context}: {relative_path}"))

        except Exception:
            continue

    return patterns


def _convert_to_django_pattern(url_path: str) -> str | None:
    """Convert file path to Django URL pattern."""
    if not url_path:
        return ""

    # simple conversion - replace [param] with <str:param> and [[args]] with <path:args>
    import re

    # handle [[args]] first
    args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")
    url_path = args_pattern.sub(r"<path:\1>", url_path)

    # handle [param]
    param_pattern = re.compile(r"\[([^\[\]]+)\]")
    url_path = param_pattern.sub(r"<str:\1>", url_path)

    return url_path
