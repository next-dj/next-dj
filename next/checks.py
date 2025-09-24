"""Django checks framework integration for next-dj file-based routing system.

This module implements comprehensive validation checks for the next-dj routing
system, ensuring proper configuration and file structure integrity. The checks
validate router configurations, page file structures, template availability,
and URL pattern generation to prevent runtime errors and configuration issues.

All checks are integrated with Django's built-in checks framework, making them
available through standard Django management commands like 'check' and 'runserver'.
"""

import ast
import importlib.util
import inspect
import re
import types
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from django.apps import apps
from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from .pages import _load_python_module
from .urls import RouterBackend, RouterFactory, RouterManager, URLPatternParser


if TYPE_CHECKING:  # pragma: no cover
    from .urls import FileRouterBackend


# Expected number of parts when splitting parameter by colon
EXPECTED_PARAMETER_PARTS = 2


def _get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Initialize RouterManager with error handling.

    Returns a tuple of (router_manager, errors) where router_manager is None
    if initialization fails and errors contains any error messages.
    """
    try:
        router_manager = RouterManager()
        router_manager._reload_config()
    except (ImportError, AttributeError) as e:
        error = Error(
            f"Error initializing router manager: {e}",
            obj=settings,
            id="next.E007",
        )
        return None, [error]
    else:
        return router_manager, []


def _validate_config_structure(
    config: object,
    index: int,
) -> list[CheckMessage]:
    """Validate basic structure of a single configuration."""
    errors: list[CheckMessage] = []

    if not isinstance(config, dict):
        errors.append(
            Error(
                f"NEXT_PAGES[{index}] must be a dictionary.",
                obj=settings,
                id="next.E002",
            ),
        )
        return errors

    # check required fields
    if "BACKEND" not in config:
        errors.append(
            Error(
                f"NEXT_PAGES[{index}] must specify a BACKEND.",
                obj=settings,
                id="next.E003",
            ),
        )

    return errors


def _validate_config_fields(config: dict, index: int) -> list[CheckMessage]:
    """Validate specific fields of a configuration."""
    errors: list[CheckMessage] = []

    # check backend validity
    if (backend := config.get("BACKEND")) and backend not in RouterFactory._backends:
        errors.append(
            Error(
                f'NEXT_PAGES[{index}] specifies unknown backend "{backend}".',
                obj=settings,
                id="next.E004",
            ),
        )

    # check APP_DIRS
    if not isinstance((config.get("APP_DIRS", True)), bool):
        errors.append(
            Error(
                f"NEXT_PAGES[{index}].APP_DIRS must be a boolean.",
                obj=settings,
                id="next.E005",
            ),
        )

    # check OPTIONS
    if not isinstance((config.get("OPTIONS", {})), dict):
        errors.append(
            Error(
                f"NEXT_PAGES[{index}].OPTIONS must be a dictionary.",
                obj=settings,
                id="next.E006",
            ),
        )

    return errors


@register(Tags.compatibility)
def check_next_pages_configuration(*_args, **_kwargs) -> list[CheckMessage]:
    """Validate NEXT_PAGES configuration settings for correctness and completeness.

    Performs comprehensive validation of the NEXT_PAGES configuration dictionary,
    checking for required fields, valid backend types, and proper option structures.
    Validates that all configured backends can be instantiated successfully and
    that the configuration follows the expected schema.
    """
    # check if NEXT_PAGES is defined
    if (next_pages := getattr(settings, "NEXT_PAGES", None)) is None:
        return []  # no configuration means default will be used

    if not isinstance(next_pages, list):
        return [
            Error(
                "NEXT_PAGES must be a list of configuration dictionaries.",
                obj=settings,
                id="next.E001",
            ),
        ]

    # validate each configuration using helper functions
    errors: list[CheckMessage] = []
    for i, config in enumerate(next_pages):
        errors.extend(_validate_config_structure(config, i))
        if isinstance(config, dict):  # only validate fields if structure is valid
            errors.extend(_validate_config_fields(config, i))

    return errors


@register(Tags.compatibility)
def check_pages_structure(*_args, **_kwargs) -> list[CheckMessage]:
    """Validate pages directory structure and file organization.

    Scans all configured router backends to identify pages directories and
    validates their structure. Checks for proper file organization, naming
    conventions, and potential conflicts in URL pattern generation. Provides
    warnings for structural issues that might cause problems during runtime.
    """
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    for router in router_manager._routers:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _check_app_pages(router, errors, warnings)
            else:
                _check_root_pages(router, errors, warnings)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error checking router pages: {e}",
                    obj=settings,
                    id="next.E007",
                ),
            )

    return errors + warnings


def _check_app_pages(
    router: RouterBackend,
    errors: list[CheckMessage],
    warnings: list[CheckMessage],
) -> None:
    """Check app pages for router."""
    if not hasattr(router, "_get_installed_apps"):
        return

    file_router = cast("FileRouterBackend", router)

    for app_name in file_router._get_installed_apps():
        if not hasattr(file_router, "_get_app_pages_path"):
            continue

        pages_path = file_router._get_app_pages_path(app_name)
        if not pages_path or not hasattr(file_router, "pages_dir"):
            continue

        app_errors, app_warnings = _check_pages_directory(
            pages_path,
            f"App '{app_name}'",
            file_router.pages_dir,
        )
        errors.extend(app_errors)
        warnings.extend(app_warnings)


def _check_root_pages(
    router: RouterBackend,
    errors: list[CheckMessage],
    warnings: list[CheckMessage],
) -> None:
    """Check root pages for router."""
    if not hasattr(router, "_get_root_pages_path"):
        return

    # type assertion: we know this is a FileRouterBackend in practice
    file_router: FileRouterBackend = router  # type: ignore[assignment]

    pages_path = file_router._get_root_pages_path()
    if not pages_path or not hasattr(file_router, "pages_dir"):
        return

    root_errors, root_warnings = _check_pages_directory(
        pages_path,
        "Root",
        file_router.pages_dir,
    )
    errors.extend(root_errors)
    warnings.extend(root_warnings)


def _check_directory_syntax(pages_path: Path, context: str) -> list[CheckMessage]:
    """Check directory names for valid syntax."""
    errors: list[CheckMessage] = []

    for item in pages_path.rglob("*"):
        if not item.is_dir():
            continue

        dir_name_str = item.name
        relative_path = item.relative_to(pages_path)

        # check for invalid parameter syntax
        if dir_name_str.startswith("[") and dir_name_str.endswith("]"):
            if not _is_valid_parameter_syntax(dir_name_str):
                errors.append(
                    Error(
                        f"{context} pages: Invalid parameter syntax "
                        f'"{dir_name_str}" in {relative_path}. '
                        f"Use [param] or [type:param] format.",
                        obj=settings,
                        id="next.E008",
                    ),
                )

        # check for invalid args syntax
        elif dir_name_str.startswith("[[") and dir_name_str.endswith("]]"):
            if not _is_valid_args_syntax(dir_name_str):
                errors.append(
                    Error(
                        f"{context} pages: Invalid args syntax "
                        f'"{dir_name_str}" in {relative_path}. '
                        f"Use [[args]] format.",
                        obj=settings,
                        id="next.E009",
                    ),
                )

        # check for incomplete parameter/args syntax
        elif dir_name_str.startswith("["):
            # incomplete args syntax
            errors.append(
                Error(
                    f"{context} pages: Incomplete args syntax "
                    f'"{dir_name_str}" in {relative_path}. '
                    f"Use [[args]] format.",
                    obj=settings,
                    id="next.E009",
                ),
            )

    return errors


def _check_missing_page_files(pages_path: Path, context: str) -> list[CheckMessage]:
    """Check for missing page.py files in parameter directories."""
    errors: list[CheckMessage] = []

    for item in pages_path.rglob("*"):
        if not item.is_dir():
            continue

        dir_name_str = item.name
        # check all parameter directories for missing page.py
        if (dir_name_str.startswith("[") and dir_name_str.endswith("]")) or (
            dir_name_str.startswith("[[") and dir_name_str.endswith("]]")
        ):
            page_file = item / "page.py"
            if not page_file.exists():
                errors.append(
                    Error(
                        f"{context} pages: Parameter directory "
                        f'"{item.relative_to(pages_path)}" is missing page.py file.',
                        obj=settings,
                        id="next.E010",
                    ),
                )

    return errors


def _check_pages_directory(
    pages_path: Path,
    context: str,
    _dir_name: str,
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check a specific pages directory for issues."""
    if not pages_path.exists():
        return [], []

    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    # check directory syntax
    errors.extend(_check_directory_syntax(pages_path, context))

    # check for missing page files
    errors.extend(_check_missing_page_files(pages_path, context))

    return errors, warnings


def _is_valid_parameter_syntax(param_str: str) -> bool:
    """Check if parameter syntax is valid."""
    if not (param_str.startswith("[") and param_str.endswith("]")):
        return False

    content = param_str[1:-1]
    if ":" in content:
        parts = content.split(":", 1)
        if len(parts) != EXPECTED_PARAMETER_PARTS:
            return False
        type_name, param_name = parts
        # check that there are no additional colons
        if ":" in param_name:
            return False
        return bool(type_name.strip() and param_name.strip())
    return bool(content.strip())


def _is_valid_args_syntax(args_str: str) -> bool:
    """Check if args syntax is valid."""
    if not (args_str.startswith("[[") and args_str.endswith("]]")):
        return False

    content = args_str[2:-2]
    return bool(content.strip())


@register(Tags.compatibility)
def check_page_functions(*_args, **_kwargs) -> list[CheckMessage]:
    """Validate page.py files for proper function definitions and structure.

    Scans all page.py files in configured pages directories to ensure they
    contain valid render functions or proper template definitions. Validates
    function signatures, return types, and argument handling to prevent
    runtime errors during page rendering.
    """
    errors: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors

    for router in router_manager._routers:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _check_app_page_functions(router, errors)
            else:
                _check_root_page_functions(router, errors)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error checking page functions: {e}",
                    obj=settings,
                    id="next.E011",
                ),
            )

    return errors


def _check_app_page_functions(
    router: RouterBackend,
    errors: list[CheckMessage],
) -> None:
    """Check app page functions for router."""
    if not hasattr(router, "_get_installed_apps"):
        return

    # type assertion: we know this is a FileRouterBackend in practice
    file_router: FileRouterBackend = router  # type: ignore[assignment]

    for app_name in file_router._get_installed_apps():
        if not hasattr(file_router, "_get_app_pages_path"):
            continue

        pages_path = file_router._get_app_pages_path(app_name)
        if not pages_path:
            continue

        app_errors = _check_page_functions_in_directory(pages_path, f"App '{app_name}'")
        errors.extend(app_errors)


def _check_root_page_functions(
    router: RouterBackend,
    errors: list[CheckMessage],
) -> None:
    """Check root page functions for router."""
    if not hasattr(router, "_get_root_pages_path"):
        return

    # type assertion: we know this is a FileRouterBackend in practice
    file_router: FileRouterBackend = router  # type: ignore[assignment]

    pages_path = file_router._get_root_pages_path()
    if not pages_path:
        return

    root_errors = _check_page_functions_in_directory(pages_path, "Root")
    errors.extend(root_errors)


def _check_page_functions_in_directory(
    pages_path: Path,
    context: str,
) -> list[CheckMessage]:
    """Check page.py files in a directory for valid render functions or templates."""
    errors: list[CheckMessage] = []

    if not pages_path.exists():
        return errors

    for page_file in pages_path.rglob("page.py"):
        # check if page has render function
        render_func = _load_render_function(page_file)

        # check if page has template or template.djx
        has_template = _has_template_or_djx(page_file)

        if render_func is None and not has_template:
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    "is missing a valid render function, template attribute, "
                    "or template.djx file.",
                    obj=settings,
                    id="next.E012",
                ),
            )
        elif render_func is not None and not callable(render_func):
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    f"render attribute is not callable.",
                    obj=settings,
                    id="next.E013",
                ),
            )

    return errors


def _load_render_function(file_path: Path) -> object:
    """Load render function from page.py file."""
    try:
        if (
            spec := importlib.util.spec_from_file_location("page_module", file_path)
        ) is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        return getattr(module, "render", None)
    except (ImportError, AttributeError, OSError, SyntaxError):
        return None


def _has_template_or_djx(file_path: Path) -> bool:
    """Check if page.py has template attribute or template.djx file exists."""
    try:
        if (
            spec := importlib.util.spec_from_file_location("page_module", file_path)
        ) is None or spec.loader is None:
            return False

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # check for template attribute
        if hasattr(module, "template"):
            return True

        # check for template.djx file
        djx_file = file_path.parent / "template.djx"
        return djx_file.exists()

    except (ImportError, AttributeError, OSError, SyntaxError):
        return False


@register(Tags.compatibility)
def check_missing_templates(*_args, **_kwargs) -> list[CheckMessage]:
    """Validate template availability for all page.py files.

    Ensures that every page.py file has either a template attribute defined
    or a corresponding template.djx file. This check prevents pages from
    being created without proper template definitions, which would cause
    rendering errors during runtime.
    """
    errors: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors

    for router in router_manager._routers:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _check_app_missing_templates(router, errors)
            else:
                _check_root_missing_templates(router, errors)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error checking missing templates: {e}",
                    obj=settings,
                    id="next.E017",
                ),
            )

    return errors


def _check_app_missing_templates(
    router: RouterBackend,
    errors: list[CheckMessage],
) -> None:
    """Check app missing templates for router."""
    if not hasattr(router, "_get_installed_apps"):
        return

    # type assertion: we know this is a FileRouterBackend in practice
    file_router: FileRouterBackend = router  # type: ignore[assignment]

    for app_name in file_router._get_installed_apps():
        if not hasattr(file_router, "_get_app_pages_path"):
            continue

        pages_path = file_router._get_app_pages_path(app_name)
        if not pages_path:
            continue

        app_errors = _check_missing_templates_in_directory(
            pages_path,
            f"App '{app_name}'",
        )
        errors.extend(app_errors)


def _check_root_missing_templates(
    router: RouterBackend,
    errors: list[CheckMessage],
) -> None:
    """Check root missing templates for router."""
    if not hasattr(router, "_get_root_pages_path"):
        return

    pages_path = router._get_root_pages_path()
    if not pages_path:
        return

    root_errors = _check_missing_templates_in_directory(pages_path, "Root")
    errors.extend(root_errors)


def _check_missing_templates_in_directory(
    pages_path: Path,
    context: str,
) -> list[CheckMessage]:
    """Check for missing templates in page.py files."""
    errors: list[CheckMessage] = []

    if not pages_path.exists():
        return errors

    for page_file in pages_path.rglob("page.py"):
        # check if page has template or template.djx
        has_template = _has_template_or_djx(page_file)

        if not has_template:
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    "is missing a template attribute or template.djx file.",
                    obj=settings,
                    id="next.E018",
                ),
            )

    return errors


def _check_layout_file(layout_file: Path) -> CheckMessage | None:
    """Check if layout file has required template block."""
    try:
        content = layout_file.read_text(encoding="utf-8")
        if "{% block template %}" not in content:
            return DjangoWarning(
                f"Layout file {layout_file} does not contain required "
                "{% block template %} block. "
                "This may cause template inheritance issues.",
                obj=str(layout_file),
                id="next.W001",
            )
    except (OSError, UnicodeDecodeError):
        pass
    return None


@register(Tags.compatibility)
def check_layout_templates(*_args, **_kwargs) -> list[CheckMessage]:
    """Check layout.djx files for proper template block structure.

    Validates that layout.djx files contain the required {% block template %}
    structure for proper inheritance. This check can be disabled by setting
    NEXT_PAGES_OPTIONS.check_layout_template_blocks = False.
    """
    warnings: list[CheckMessage] = []

    # check if this check is disabled
    next_pages_options = getattr(settings, "NEXT_PAGES_OPTIONS", {})
    if not next_pages_options.get("check_layout_template_blocks", True):
        return warnings

    try:
        router_manager = RouterManager()
        router_manager._reload_config()
    except (ImportError, AttributeError):
        return warnings

    for router in router_manager._routers:
        if not hasattr(router, "_scan_pages_directory"):
            continue

        pages_dir = _get_pages_directory(router)
        if not pages_dir:
            continue

        for _url_path, page_path in router._scan_pages_directory(pages_dir):
            layout_file = page_path.parent / "layout.djx"
            if not layout_file.exists():
                continue

            warning = _check_layout_file(layout_file)
            if warning:
                warnings.append(warning)

    return warnings


def _has_page_content(page_path: Path) -> bool:
    """Check if page has any content (template, render, template.djx, layout.djx)."""
    # check if page.py has content
    module = _load_python_module(page_path)
    has_template = False
    has_render = False

    if module:
        has_template = hasattr(module, "template")
        has_render = hasattr(module, "render") and callable(module.render)

    # check for template.djx
    template_djx = page_path.parent / "template.djx"
    has_template_djx = template_djx.exists()

    # check for layout.djx
    layout_djx = page_path.parent / "layout.djx"
    has_layout_djx = layout_djx.exists()

    return any([has_template, has_render, has_template_djx, has_layout_djx])


@register(Tags.compatibility)
def check_missing_page_content(*_args, **_kwargs) -> list[CheckMessage]:
    """Check for page.py files that have no content (no template, no render function).

    Validates that page.py files have either a template variable, template.djx file,
    layout.djx file, or a render function. This check can be disabled by setting
    NEXT_PAGES_OPTIONS.check_missing_page_content = False.
    """
    warnings: list[CheckMessage] = []

    # check if this check is disabled
    next_pages_options = getattr(settings, "NEXT_PAGES_OPTIONS", {})
    if not next_pages_options.get("check_missing_page_content", True):
        return warnings

    try:
        router_manager = RouterManager()
        router_manager._reload_config()
    except (ImportError, AttributeError):
        return warnings

    for router in router_manager._routers:
        if not hasattr(router, "_scan_pages_directory"):
            continue

        pages_dir = _get_pages_directory(router)
        if not pages_dir:
            continue

        for _url_path, page_path in router._scan_pages_directory(pages_dir):
            if not page_path.exists():
                continue

            if not _has_page_content(page_path):
                warnings.append(
                    DjangoWarning(
                        f"Page file {page_path} has no content: no template variable, "
                        "no render function, no template.djx, and no layout.djx found. "
                        "This page will not render anything.",
                        obj=str(page_path),
                        id="next.W002",
                    ),
                )

    return warnings


def _get_duplicate_parameters(url_path: str, parser: URLPatternParser) -> list[str]:
    """Get list of duplicate parameter names in URL path."""
    param_matches = parser._param_pattern.findall(url_path)
    param_names = []
    for param_str in param_matches:
        param_name, _ = parser._parse_param_name_and_type(param_str)
        param_names.append(param_name)

    if len(param_names) == len(set(param_names)):
        return []

    return [name for name in set(param_names) if param_names.count(name) > 1]


@register(Tags.compatibility)
def check_duplicate_url_parameters(*_args, **_kwargs) -> list[CheckMessage]:
    """Check for duplicate parameter names in URL patterns.

    Validates that URL patterns don't have duplicate parameter names like
    /page/[id]/[id]/ which would cause conflicts. This check can be disabled by setting
    NEXT_PAGES_OPTIONS.check_duplicate_url_parameters = False.
    """
    errors: list[CheckMessage] = []

    # check if this check is disabled
    next_pages_options = getattr(settings, "NEXT_PAGES_OPTIONS", {})
    if not next_pages_options.get("check_duplicate_url_parameters", True):
        return errors

    try:
        router_manager = RouterManager()
        router_manager._reload_config()
    except (ImportError, AttributeError):
        return errors

    parser = URLPatternParser()

    for router in router_manager._routers:
        if not hasattr(router, "_scan_pages_directory"):
            continue

        pages_dir = _get_pages_directory(router)
        if not pages_dir:
            continue

        for url_path, page_path in router._scan_pages_directory(pages_dir):
            if not page_path.exists():
                continue

            try:
                django_pattern, parameters = parser.parse_url_pattern(url_path)
                duplicates = _get_duplicate_parameters(url_path, parser)

                if duplicates:
                    errors.append(
                        Error(
                            f"URL pattern '{url_path}' has duplicate parameter "
                            f"names: {duplicates}. "
                            "Each parameter must have a unique name.",
                            obj=str(page_path),
                            id="next.E002",
                        ),
                    )
            except (ValueError, TypeError, AttributeError):
                continue

    return errors


def _has_context_decorator_without_key(func: Callable[..., Any]) -> bool:
    """Check if function has @context decorator without key."""
    try:
        source = inspect.getsource(func)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Name) and decorator.id == "context":
                        return True
    except (SyntaxError, OSError, UnicodeDecodeError):
        pass
    return False


def _get_function_result(func: Callable[..., Any]) -> object:
    """Get function result, handling arguments if needed."""
    try:
        return func()
    except TypeError:
        return func(MagicMock())


def _get_pages_directory(router: RouterBackend) -> Path | None:
    """Get pages directory path for router."""
    if not hasattr(router, "pages_dir"):
        return None

    # type assertion: we know this is a FileRouterBackend in practice
    file_router: FileRouterBackend = router  # type: ignore[assignment]

    if file_router.app_dirs:
        for app_config in apps.get_app_configs():
            app_path = Path(app_config.path)
            potential_pages_dir = app_path / str(file_router.pages_dir)
            if potential_pages_dir.exists():
                return potential_pages_dir
    else:
        pages_dir = Path(str(file_router.pages_dir))
        if pages_dir.exists():
            return pages_dir
    return None


def _check_context_function(
    func_name: str,
    func: Callable[..., Any],
    page_path: Path,
) -> CheckMessage | None:
    """Check if context function returns dictionary."""
    try:
        result = _get_function_result(func)
        if not isinstance(result, dict):
            return Error(
                f"Context function '{func_name}' in {page_path} "
                "must return a dictionary "
                f"when used with @context decorator (without key). "
                f"Got {type(result).__name__} instead.",
                obj=str(page_path),
                id="next.E003",
            )
    except (TypeError, AttributeError, OSError):
        pass
    return None


def _check_module_context_functions(
    module: types.ModuleType,
    page_path: Path,
) -> list[CheckMessage]:
    """Check context functions in a single module."""
    errors: list[CheckMessage] = []

    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not _has_context_decorator_without_key(obj):
            continue

        error = _check_context_function(name, obj, page_path)
        if error:
            errors.append(error)

    return errors


def _check_router_context_functions(router: RouterBackend) -> list[CheckMessage]:
    """Check context functions for a single router."""
    errors: list[CheckMessage] = []

    if not hasattr(router, "_scan_pages_directory"):
        return errors

    pages_dir = _get_pages_directory(router)
    if not pages_dir:
        return errors

    for _url_path, page_path in router._scan_pages_directory(pages_dir):
        if not page_path.exists():
            continue

        module = _load_python_module(page_path)
        if not module:
            continue

        module_errors = _check_module_context_functions(module, page_path)
        errors.extend(module_errors)

    return errors


@register(Tags.compatibility)
def check_context_functions(*_args, **_kwargs) -> list[CheckMessage]:
    """Check context functions for proper return types.

    Validates that context functions decorated with @context (without key)
    always return a dictionary. This check can be disabled by setting
    NEXT_PAGES_OPTIONS.check_context_return_types = False.
    """
    # check if this check is disabled
    next_pages_options = getattr(settings, "NEXT_PAGES_OPTIONS", {})
    if not next_pages_options.get("check_context_return_types", True):
        return []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors

    errors: list[CheckMessage] = []
    for router in router_manager._routers:
        router_errors = _check_router_context_functions(router)
        errors.extend(router_errors)

    return errors


@register(Tags.compatibility)  # type: ignore[type-var]
def check_url_patterns(_app_configs: list | None, **_kwargs) -> list[CheckMessage]:
    """Validate URL pattern generation and identify potential conflicts.

    Generates URL patterns from all configured router backends and validates
    them for naming conflicts, parameter consistency, and Django compatibility.
    Checks for duplicate URL names, invalid parameter types, and potential
    routing conflicts that could cause unexpected behavior.
    """
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    # collect all URL patterns
    all_patterns: list[tuple[str, str]] = []  # (pattern, source)

    for router in router_manager._routers:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _collect_app_patterns(router, all_patterns)
            else:
                _collect_root_patterns(router, all_patterns)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error collecting patterns from router: {e}",
                    obj=settings,
                    id="next.E016",
                ),
            )

    # check for conflicts
    try:
        _check_url_conflicts(all_patterns, errors, warnings)
    except (ValueError, TypeError) as e:
        errors.append(
            Error(
                f"Error checking URL conflicts: {e}",
                obj=settings,
                id="next.E016",
            ),
        )

    return errors + warnings


def _collect_app_patterns(
    router: RouterBackend,
    all_patterns: list[tuple[str, str]],
) -> None:
    """Collect URL patterns from app pages."""
    if not hasattr(router, "_get_installed_apps"):
        return

    # type assertion: we know this is a FileRouterBackend in practice
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
    """Collect URL patterns from root pages."""
    if not hasattr(router, "_get_root_pages_path"):
        return

    pages_path = router._get_root_pages_path()
    if not pages_path:
        return

    patterns = _collect_url_patterns(pages_path, "Root")
    all_patterns.extend(patterns)


def _check_url_conflicts(
    all_patterns: list[tuple[str, str]],
    errors: list[CheckMessage],
    _warnings: list[CheckMessage],
) -> None:
    """Check for URL pattern conflicts."""
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
                    f'URL pattern conflict: "{pattern}" is defined in '
                    f"multiple locations: {', '.join(sources)}",
                    obj=settings,
                    id="next.E015",
                ),
            )


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

        except (OSError, ValueError):
            # skip files that can't be processed
            continue

    return patterns


def _convert_to_django_pattern(url_path: str) -> str | None:
    """Convert file path to Django URL pattern."""
    if not url_path:
        return ""

    # handle [[args]] first
    args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")
    url_path = args_pattern.sub(r"<path:\1>", url_path)

    # handle argument [param]
    param_pattern = re.compile(r"\[([^\[\]]+)\]")
    return param_pattern.sub(r"<str:\1>", url_path)
