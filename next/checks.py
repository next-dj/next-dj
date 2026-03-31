"""Register ``manage.py check`` hooks for next-dj.

Covers settings, file routes, templates, URLs, and components.
"""

import ast
import importlib.util
import inspect
import re
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.utils.module_loading import import_string

from .components import ComponentsManager, FileComponentsBackend
from .conf import next_framework_settings
from .pages import _load_python_module
from .urls import (
    FileRouterBackend,
    RouterBackend,
    RouterFactory,
    RouterManager,
    URLPatternParser,
)


# Expected number of parts when splitting parameter by colon
EXPECTED_PARAMETER_PARTS = 2


def _get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Fresh ``RouterManager`` or init errors for ``check`` messages."""
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
    """Validate required keys and types for one ``DEFAULT_PAGE_BACKENDS`` entry."""
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

    # check required fields
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


FILE_ROUTER_BACKEND = "next.urls.FileRouterBackend"

_PAGE_BACKEND_SETTINGS_KEY = "DEFAULT_PAGE_BACKENDS"


def _router_backend_path_is_valid(backend_path: str) -> bool:
    """Whether ``backend_path`` names a registered or importable ``RouterBackend``."""
    if backend_path in RouterFactory._backends:
        return True
    try:
        resolved = import_string(backend_path)
    except ImportError:
        return False
    return isinstance(resolved, type) and issubclass(resolved, RouterBackend)


def _validate_file_router_backend_fields(  # noqa: C901, PLR0912
    config: dict,
    index: int,
) -> list[CheckMessage]:
    """Validate ``DIRS``, ``PAGES_DIR``, ``APP_DIRS``, ``OPTIONS`` (file router)."""
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

    if "COMPONENTS_DIR" in config and not isinstance(config["COMPONENTS_DIR"], str):
        errors.append(
            Error(
                f"{rf_routers}.COMPONENTS_DIR must be a string.",
                obj=settings,
                id="next.E027",
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
                    "OPTIONS only supports context_processors; use top-level DIRS and "
                    "COMPONENTS_DIR for paths.",
                    obj=settings,
                    id="next.E006",
                ),
            )
            break

    return errors


def _validate_config_fields(config: dict, index: int) -> list[CheckMessage]:
    """Validate specific fields of a configuration."""
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

    if backend == FILE_ROUTER_BACKEND:
        errors.extend(_validate_file_router_backend_fields(config, index))

    return errors


REQUEST_CONTEXT_PROCESSOR = "django.template.context_processors.request"


@register(Tags.templates)
def check_request_in_context(*_args, **_kwargs) -> list[CheckMessage]:
    """Ensure ``request`` in template context (required for ``{% form %}``)."""
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
def check_next_pages_configuration(*_args, **_kwargs) -> list[CheckMessage]:
    """Validate ``DEFAULT_PAGE_BACKENDS`` inside merged ``NEXT_FRAMEWORK``."""
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
        if isinstance(config, dict):  # only validate fields if structure is valid
            errors.extend(_validate_config_fields(config, i))

    return errors


_COMPONENT_BACKEND_SETTINGS_KEY = "DEFAULT_COMPONENT_BACKENDS"


def _validate_single_component_backend(
    config: dict,
    index: int,
) -> list[CheckMessage]:
    """Validate required keys and types for one merged component backend dict."""
    prefix = f"NEXT_FRAMEWORK['{_COMPONENT_BACKEND_SETTINGS_KEY}'][{index}]"
    errors: list[CheckMessage] = [
        Error(
            f"{prefix} must specify {key}.",
            obj=settings,
            id="next.E031",
        )
        for key in ("BACKEND", "DIRS", "COMPONENTS_DIR")
        if key not in config
    ]
    if errors:
        return errors
    if not isinstance(config["BACKEND"], str):
        errors.append(
            Error(
                f"{prefix}.BACKEND must be a string.",
                obj=settings,
                id="next.E032",
            ),
        )
    if not isinstance(config["DIRS"], list):
        errors.append(
            Error(
                f"{prefix}.DIRS must be a list.",
                obj=settings,
                id="next.E032",
            ),
        )
    if not isinstance(config["COMPONENTS_DIR"], str):
        errors.append(
            Error(
                f"{prefix}.COMPONENTS_DIR must be a string.",
                obj=settings,
                id="next.E027",
            ),
        )
    return errors


@register(Tags.compatibility)
def check_next_components_configuration(*_args, **_kwargs) -> list[CheckMessage]:
    """Validate ``DEFAULT_COMPONENT_BACKENDS`` shape in merged ``NEXT_FRAMEWORK``."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is not None and not isinstance(raw, dict):
        return []

    backends = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(backends, list):
        return [
            Error(
                "NEXT_FRAMEWORK['DEFAULT_COMPONENT_BACKENDS'] must be a list of "
                "backend configuration dictionaries.",
                obj=settings,
                id="next.E023",
            ),
        ]

    if len(backends) == 0:
        return [
            Error(
                "NEXT_FRAMEWORK['DEFAULT_COMPONENT_BACKENDS'] must contain at least "
                "one component backend entry.",
                obj=settings,
                id="next.E033",
            ),
        ]

    errors: list[CheckMessage] = []
    for i, config in enumerate(backends):
        if not isinstance(config, dict):
            errors.append(
                Error(
                    f"NEXT_FRAMEWORK['{_COMPONENT_BACKEND_SETTINGS_KEY}'][{i}] "
                    "must be a dictionary.",
                    obj=settings,
                    id="next.E002",
                ),
            )
            continue
        errors.extend(_validate_single_component_backend(config, i))

    return errors


@register(Tags.compatibility)
def check_pages_structure(*_args, **_kwargs) -> list[CheckMessage]:
    """Check pages trees from each router for layouts, naming, and structure."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
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
    """Check root pages for router (all paths from ``_get_root_pages_paths``)."""
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
    """Check for missing ``page.py`` files in parameter directories."""
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
    """Validate each page module for ``render`` and ``template``. Warn when empty."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
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
    """Check root page functions for router (all root paths)."""
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
    """Check ``page.py`` files: ``render``/``template`` rules and empty-page warning."""
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
    """Load ``render`` function from ``page.py`` file."""
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
    """Check if ``page.py`` has ``template`` or sibling ``template.djx`` exists."""
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


def _has_page_content(page_path: Path) -> bool:
    """Whether ``page.py`` has ``template``, ``render``, or sibling djx files."""
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
    """Check if layout file has required ``{% block template %}``."""
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
def check_layout_templates(*_args, **_kwargs) -> list[CheckMessage]:
    """Check ``layout.djx`` files for proper template block structure.

    Validates that ``layout.djx`` files contain the required ``{% block template %}``
    structure for proper inheritance.
    """
    warnings: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    for router in router_manager._backends:
        for _url_path, page_path in _iter_scanned_page_pairs(router):
            layout_file = page_path.parent / "layout.djx"
            if not layout_file.exists():
                continue

            warning = _check_layout_file(layout_file)
            if warning:
                warnings.append(warning)

    return warnings


def _get_duplicate_parameters(url_path: str, parser: URLPatternParser) -> list[str]:
    """Parameter names that appear more than once in bracket segments."""
    param_matches = parser._param_pattern.findall(url_path)
    param_names = []
    for param_str in param_matches:
        param_name, _ = parser._parse_param_name_and_type(param_str)
        param_names.append(param_name)

    if len(param_names) == len(set(param_names)):
        return []

    return [name for name in set(param_names) if param_names.count(name) > 1]


@register(Tags.urls)
def check_duplicate_url_parameters(*_args, **_kwargs) -> list[CheckMessage]:
    """Fail when the same bracket parameter name is repeated in one route."""
    errors: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors

    parser = URLPatternParser()

    for router in router_manager._backends:
        for url_path, page_path in _iter_scanned_page_pairs(router):
            if not page_path.exists():
                continue

            try:
                _django_pattern, _parameters = parser.parse_url_pattern(url_path)
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


def _has_context_decorator_without_key(func: Callable[..., Any]) -> bool:
    """Check if function has ``@context`` decorator without key."""
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
    """Call ``func()`` with no args or with a stub when it needs arguments."""
    try:
        return func()
    except TypeError:
        # Minimal stand-in for view-like context callables that expect one argument.
        stub = types.SimpleNamespace()
        return func(stub)


def _get_first_root_pages_path(file_router: FileRouterBackend) -> Path | None:
    """Return the first entry from ``_get_root_pages_paths`` when defined."""
    if not hasattr(file_router, "_get_root_pages_paths"):
        return None
    root_paths = file_router._get_root_pages_paths()
    return root_paths[0] if root_paths else None


def _get_first_app_pages_dir(file_router: FileRouterBackend) -> Path | None:
    """Return first existing app ``pages`` dir, or ``None``."""
    for app_config in apps.get_app_configs():
        potential = Path(app_config.path) / str(file_router.pages_dir)
        if potential.exists():
            return potential
    return None


def _get_pages_directory(router: RouterBackend) -> Path | None:
    """Pick one representative pages root directory for scanning checks."""
    if not hasattr(router, "pages_dir"):
        return None
    file_router: FileRouterBackend = router  # type: ignore[assignment]
    if file_router.app_dirs:
        return _get_first_app_pages_dir(file_router) or _get_first_root_pages_path(
            file_router
        )
    p = Path(str(file_router.pages_dir))
    return _get_first_root_pages_path(file_router) or (p if p.exists() else None)


def _iter_scanned_page_pairs(
    router: RouterBackend,
) -> Iterator[tuple[str, Path]]:
    """Yield pairs from ``_scan_pages_directory`` when the router is scannable."""
    if not hasattr(router, "_scan_pages_directory"):
        return
    pages_dir = _get_pages_directory(router)
    if not pages_dir:
        return
    yield from router._scan_pages_directory(pages_dir)


def _check_context_function(
    func_name: str,
    func: Callable[..., Any],
    page_path: Path,
) -> CheckMessage | None:
    """Emit an error when keyless context callables do not produce a ``dict``."""
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
    """Collect keyless ``@context`` functions declared in one page module."""
    errors: list[CheckMessage] = []

    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not _has_context_decorator_without_key(obj):
            continue

        error = _check_context_function(name, obj, page_path)
        if error:
            errors.append(error)

    return errors


def _check_router_context_functions(router: RouterBackend) -> list[CheckMessage]:
    """All ``page.py`` modules under one router's pages tree."""
    errors: list[CheckMessage] = []

    for _url_path, page_path in _iter_scanned_page_pairs(router):
        if not page_path.exists():
            continue

        module = _load_python_module(page_path)
        if not module:
            continue

        module_errors = _check_module_context_functions(module, page_path)
        errors.extend(module_errors)

    return errors


@register(Tags.templates)
def check_context_functions(*_args, **_kwargs) -> list[CheckMessage]:
    """Keyless ``@context`` callables must yield a dict when invoked."""
    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors

    errors: list[CheckMessage] = []
    for router in router_manager._backends:
        router_errors = _check_router_context_functions(router)
        errors.extend(router_errors)

    return errors


@register(Tags.urls)
def check_url_patterns(*_args, **_kwargs) -> list[CheckMessage]:
    """Collect patterns from routers and flag duplicate Django path strings."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = _get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    # collect all URL patterns
    all_patterns: list[tuple[str, str]] = []  # (pattern, source)

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

    # check for conflicts
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
    """Append patterns discovered under each app's ``pages_dir``."""
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
    """Errors when the same Django path string comes from multiple sources."""
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
    """Bracket syntax to ``<str:>`` / ``<path:>`` for conflict comparison."""
    if not url_path:
        return ""

    # handle [[args]] first
    args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")
    url_path = args_pattern.sub(r"<path:\1>", url_path)

    # handle argument [param]
    param_pattern = re.compile(r"\[([^\[\]]+)\]")
    return param_pattern.sub(r"<str:\1>", url_path)


@register(Tags.compatibility)
def check_duplicate_component_names(*_args, **_kwargs) -> list[CheckMessage]:
    """Check that no two components share the same name within the same scope."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    manager = ComponentsManager()
    manager._reload_config()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        backend._ensure_loaded()
        seen: dict[tuple[Path, str], list[tuple[str, str]]] = {}

        for info in backend._registry:
            key = (info.scope_root, info.name)
            if key not in seen:
                seen[key] = []
            path_str = str(info.template_path or info.module_path or "")
            seen[key].append((info.scope_relative, path_str))

        for (_scope_root, name), entries in seen.items():
            if len(entries) > 1:
                paths_str = ", ".join(p for _sr, p in entries if p)
                errors.append(
                    Error(
                        f'Component name "{name}" is registered more than once '
                        f"within the same scope: {paths_str}",
                        obj=settings,
                        id="next.E020",
                    ),
                )
    return errors


def _component_py_uses_pages_context(file_path: Path) -> bool:
    """Return True if ``component.py`` imports ``context`` from ``next.pages``."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and getattr(node, "module", None) == "next.pages"
        ):
            for alias in node.names:
                if getattr(alias, "name", None) == "context":
                    return True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "page"
            and node.attr == "context"
        ):
            return True
    return False


@register(Tags.compatibility)
def check_component_py_no_pages_context(*_args, **_kwargs) -> list[CheckMessage]:
    """Check that ``component.py`` files do not use ``context`` from ``next.pages``."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    manager = ComponentsManager()
    manager._reload_config()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        backend._ensure_loaded()

        for info in backend._registry:
            if info.module_path is None:
                continue
            if not info.module_path.exists():
                continue
            if _component_py_uses_pages_context(info.module_path):
                errors.append(
                    Error(
                        "component.py must not use context from next.pages. "
                        "Use component context from next.components instead.",
                        obj=str(info.module_path),
                        id="next.E021",
                    ),
                )
    return errors
