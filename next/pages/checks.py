"""System checks for the pages subsystem."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import types
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks.common import get_router_manager, iter_scanned_page_pairs

from .loaders import _load_python_module


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.urls import FileRouterBackend, RouterBackend


REQUEST_CONTEXT_PROCESSOR = "django.template.context_processors.request"

EXPECTED_PARAMETER_PARTS = 2


@register(Tags.templates)
def check_request_in_context(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Ensure `request` is in the template context (required for `{% form %}`)."""
    if "next" not in settings.INSTALLED_APPS:
        return []

    errors: list[CheckMessage] = []
    templates = getattr(settings, "TEMPLATES", [])

    for i, config in enumerate(templates):
        if not isinstance(config, dict):
            continue
        options = config.get("OPTIONS", {})
        processors = options.get("context_processors", [])
        if REQUEST_CONTEXT_PROCESSOR not in processors:
            msg = (
                f"TEMPLATES[{i}]: 'request' must be in template context "
                "when using next (required for {% form %} and CSRF). Add "
                "'django.template.context_processors.request' to "
                "OPTIONS.context_processors."
            )
            errors.append(
                Error(
                    msg,
                    obj=settings,
                    id="next.E019",
                ),
            )
    return errors


@register(Tags.compatibility)
def check_pages_structure(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Check each router's pages tree for layouts, naming, and structure."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    for router in router_manager._backends:
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
                    id="next.E030",
                ),
            )

    return errors + warnings


def _check_app_pages(
    router: RouterBackend,
    errors: list[CheckMessage],
    warnings: list[CheckMessage],
) -> None:
    """Check app pages for `router`."""
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
    """Check root pages for `router` across all configured root paths."""
    if not hasattr(router, "_get_root_pages_paths"):
        return
    if not hasattr(router, "pages_dir"):
        return
    file_router = cast("FileRouterBackend", router)
    for i, pages_path in enumerate(router._get_root_pages_paths()):
        context = "Root" if i == 0 else f"Root ({pages_path})"
        root_errors, root_warnings = _check_pages_directory(
            pages_path,
            context,
            file_router.pages_dir,
        )
        errors.extend(root_errors)
        warnings.extend(root_warnings)


def _check_directory_syntax(pages_path: Path, context: str) -> list[CheckMessage]:
    """Check directory names under `pages_path` for valid bracket syntax."""
    errors: list[CheckMessage] = []

    for item in pages_path.rglob("*"):
        if not item.is_dir():
            continue

        dir_name_str = item.name
        relative_path = item.relative_to(pages_path)

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

        elif dir_name_str.startswith("["):
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
    """Check for missing `page.py` files inside parameter directories."""
    errors: list[CheckMessage] = []

    for item in pages_path.rglob("*"):
        if not item.is_dir():
            continue

        dir_name_str = item.name
        if (dir_name_str.startswith("[") and dir_name_str.endswith("]")) or (
            dir_name_str.startswith("[[") and dir_name_str.endswith("]]")
        ):
            page_file = item / "page.py"
            layout_file = item / "layout.djx"
            template_file = item / "template.djx"

            if page_file.exists() or layout_file.exists() or template_file.exists():
                continue

            # Check if parameter directory has child routes
            has_child_routes = False
            for child in item.iterdir():
                if child.is_dir() and (child / "page.py").exists():
                    has_child_routes = True
                    break

            if not has_child_routes:
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

    errors.extend(_check_directory_syntax(pages_path, context))
    errors.extend(_check_missing_page_files(pages_path, context))

    return errors, warnings


def _is_valid_parameter_syntax(param_str: str) -> bool:
    """Return True when single-bracket parameter syntax is valid."""
    if not (param_str.startswith("[") and param_str.endswith("]")):
        return False

    content = param_str[1:-1]
    if ":" in content:
        parts = content.split(":", 1)
        if len(parts) != EXPECTED_PARAMETER_PARTS:
            return False
        type_name, param_name = parts
        if ":" in param_name:
            return False
        return bool(type_name.strip() and param_name.strip())
    return bool(content.strip())


def _is_valid_args_syntax(args_str: str) -> bool:
    """Return True when double-bracket args syntax is valid."""
    if not (args_str.startswith("[[") and args_str.endswith("]]")):
        return False

    content = args_str[2:-2]
    return bool(content.strip())


@register(Tags.compatibility)
def check_page_functions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate each page module for `render` or `template`. Warn when empty."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    for router in router_manager._backends:
        try:
            if hasattr(router, "app_dirs") and router.app_dirs:
                _check_app_page_functions(router, errors, warnings)
            else:
                _check_root_page_functions(router, errors, warnings)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error checking page functions: {e}",
                    obj=settings,
                    id="next.E011",
                ),
            )

    return errors + warnings


def _check_app_page_functions(
    router: RouterBackend,
    errors: list[CheckMessage],
    warnings: list[CheckMessage],
) -> None:
    """Check app page functions for `router`."""
    if not hasattr(router, "_get_installed_apps"):
        return

    file_router: FileRouterBackend = router  # type: ignore[assignment]

    for app_name in file_router._get_installed_apps():
        if not hasattr(file_router, "_get_app_pages_path"):
            continue

        pages_path = file_router._get_app_pages_path(app_name)
        if not pages_path:
            continue

        e, w = _check_page_functions_in_directory(
            pages_path,
            f"App '{app_name}'",
        )
        errors.extend(e)
        warnings.extend(w)


def _check_root_page_functions(
    router: RouterBackend,
    errors: list[CheckMessage],
    warnings: list[CheckMessage],
) -> None:
    """Check root page functions for `router` across all root paths."""
    if not hasattr(router, "_get_root_pages_paths"):
        return
    for i, pages_path in enumerate(router._get_root_pages_paths()):
        context = "Root" if i == 0 else f"Root ({pages_path})"
        e, w = _check_page_functions_in_directory(pages_path, context)
        errors.extend(e)
        warnings.extend(w)


def _check_page_functions_in_directory(
    pages_path: Path,
    context: str,
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check `page.py` files for render/template rules and warn when empty."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    if not pages_path.exists():
        return errors, warnings

    for page_file in pages_path.rglob("page.py"):
        render_func = _load_render_function(page_file)
        has_template = _has_template_or_djx(page_file)
        hard_error = False

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
            hard_error = True
        elif render_func is not None and not callable(render_func):
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    f"render attribute is not callable.",
                    obj=settings,
                    id="next.E013",
                ),
            )
            hard_error = True

        if not hard_error and not _has_page_content(page_file):
            warnings.append(
                DjangoWarning(
                    f"Page file {page_file} has no content: no template variable, "
                    "no render function, no template.djx, and no layout.djx found. "
                    "This page will not render anything.",
                    obj=str(page_file),
                    id="next.W002",
                ),
            )

    return errors, warnings


def _load_render_function(file_path: Path) -> object:
    """Load the `render` callable from a `page.py` file."""
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
    """Return True when `page.py` has `template` or a sibling `template.djx`."""
    try:
        if (
            spec := importlib.util.spec_from_file_location("page_module", file_path)
        ) is None or spec.loader is None:
            return False

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "template"):
            return True

        djx_file = file_path.parent / "template.djx"
        return djx_file.exists()

    except (ImportError, AttributeError, OSError, SyntaxError):
        return False


def _has_page_content(page_path: Path) -> bool:
    """Return True when `page.py` has `template`, `render`, or sibling djx files."""
    module = _load_python_module(page_path)
    has_template = False
    has_render = False

    if module:
        has_template = hasattr(module, "template")
        has_render = hasattr(module, "render") and callable(module.render)

    template_djx = page_path.parent / "template.djx"
    has_template_djx = template_djx.exists()

    layout_djx = page_path.parent / "layout.djx"
    has_layout_djx = layout_djx.exists()

    return any([has_template, has_render, has_template_djx, has_layout_djx])


def _check_layout_file(layout_file: Path) -> CheckMessage | None:
    """Check if layout file has required `{% block template %}`."""
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


@register(Tags.templates)
def check_layout_templates(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Check `layout.djx` files for the `{% block template %}` structure."""
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    for router in router_manager._backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            layout_file = page_path.parent / "layout.djx"
            if not layout_file.exists():
                continue

            warning = _check_layout_file(layout_file)
            if warning:
                warnings.append(warning)

    return warnings


def _has_context_decorator_without_key(func: Callable[..., Any]) -> bool:
    """Return True when `func` has the `@context` decorator applied without a key."""
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
    """Call `func()` with no args or with a stub when it needs arguments."""
    try:
        return func()
    except TypeError:
        stub = types.SimpleNamespace()
        return func(stub)


def _check_context_function(
    func_name: str,
    func: Callable[..., Any],
    page_path: Path,
) -> CheckMessage | None:
    """Emit an error when keyless context callables do not produce a dict."""
    try:
        result = _get_function_result(func)
        if not isinstance(result, dict):
            return Error(
                f"Context function '{func_name}' in {page_path} "
                "must return a dictionary "
                f"when used with @context decorator (without key). "
                f"Got {type(result).__name__} instead.",
                obj=str(page_path),
                id="next.E029",
            )
    except (TypeError, AttributeError, OSError):
        pass
    return None


def _check_module_context_functions(
    module: types.ModuleType,
    page_path: Path,
) -> list[CheckMessage]:
    """Collect keyless `@context` functions declared in one page module."""
    errors: list[CheckMessage] = []

    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not _has_context_decorator_without_key(obj):
            continue

        error = _check_context_function(name, obj, page_path)
        if error:
            errors.append(error)

    return errors


def _check_router_context_functions(router: RouterBackend) -> list[CheckMessage]:
    """Return errors from `page.py` modules under one router's pages tree."""
    errors: list[CheckMessage] = []

    for _url_path, page_path in iter_scanned_page_pairs(router):
        if not page_path.exists():
            continue

        module = _load_python_module(page_path)
        if not module:
            continue

        module_errors = _check_module_context_functions(module, page_path)
        errors.extend(module_errors)

    return errors


@register(Tags.templates)
def check_context_functions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Require keyless `@context` callables to return a dict when invoked."""
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    errors: list[CheckMessage] = []
    for router in router_manager._backends:
        router_errors = _check_router_context_functions(router)
        errors.extend(router_errors)

    return errors


__all__ = [
    "check_context_functions",
    "check_layout_templates",
    "check_page_functions",
    "check_pages_structure",
    "check_request_in_context",
]
