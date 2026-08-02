"""System checks for the pages subsystem."""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_origin

from django.apps import apps
from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT
from next.checks.common import (
    PageRootsError,
    RegistrationSubject,
    first_visit,
    get_page_roots,
    get_router_manager,
    iter_scanned_page_pairs,
    read_page_roots,
    registration_file_errors,
)
from next.conf import import_class_cached, next_framework_settings
from next.utils import callable_name, walk_page_tree

from .loaders import (
    TemplateLoader,
    _load_python_module_memo,
    build_registered_loaders,
    last_load_error,
)
from .manager import page


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from next.urls import RouterManager


logger = logging.getLogger(__name__)

REQUEST_CONTEXT_PROCESSOR = "django.template.context_processors.request"

EXPECTED_PARAMETER_PARTS = 2

# A page declares a body-source conflict only when two or more sources claim it.
_MIN_CONFLICTING_BODY_SOURCES = 2

# The file router routes only this name, so a context bound anywhere else is
# dead, including one bound to the page.py next door.
_PAGE_CONTEXT_SUBJECT = RegistrationSubject(
    decorator="@context", anchor_name="page.py", render="page render", code="next.E074"
)


@register(Tags.templates, NEXT)
def check_request_in_context(*args, **kwargs) -> list[CheckMessage]:
    """Ensure `request` is in the template context (required for `{% form %}`)."""
    # Through the registry, so the check holds for an `INSTALLED_APPS` entry
    # written as the framework's `AppConfig` path.
    if not apps.is_installed("next"):
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
            errors.append(Error(msg, obj=settings, id="next.E019"))
    return errors


@register(NEXT)
def check_pages_structure(*args, **kwargs) -> list[CheckMessage]:
    """Check each router's pages tree for layouts, naming, and structure."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    # Nested and doubly-mounted roots reach one directory through several
    # page trees.
    seen: set[Path] = set()
    for router in router_manager.backends:
        # This is the one check that names a failing `page_roots`. Every other
        # reader takes the empty list, so one broken router costs one message
        # and, here, the single traceback of the run.
        try:
            roots = read_page_roots(router)
        except PageRootsError as e:
            # Only a raised failure carries a traceback, and it is the
            # backend's own. A wrong shape has no cause, and its whole
            # diagnosis is the message below.
            hint = None
            if e.__cause__ is not None:
                logger.exception("a router failed to report its page trees")
                detail = f"{e}: {e.__cause__}"
            else:
                detail = str(e)
                hint = (
                    "page_roots() returns a list of next.urls.PageRoot entries, "
                    "each pairing a directory with its label."
                )
            errors.append(Error(detail, hint=hint, obj=settings, id="next.E030"))
            continue
        try:
            for root in roots:
                root_errors, root_warnings = _check_pages_directory(
                    root.path, root.label, seen
                )
                errors.extend(root_errors)
                warnings.extend(root_warnings)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(f"Error checking router pages: {e}", obj=settings, id="next.E030")
            )

    return errors + warnings


def _configured_pages_dir_names() -> list[str]:
    """Return the `PAGES_DIR` name every `PAGE_BACKENDS` entry declares."""
    configured = next_framework_settings.PAGE_BACKENDS
    if not isinstance(configured, list):
        return []
    return [
        name
        for entry in configured
        if isinstance(entry, dict) and isinstance(name := entry.get("PAGES_DIR"), str)
        if name
    ]


def _working_directory_page_trees() -> list[Path]:
    """Return each distinct `PAGES_DIR` directory sitting beside the process."""
    cwd = Path.cwd()
    trees: dict[Path, None] = {}
    for name in _configured_pages_dir_names():
        candidate = (cwd / name).resolve()
        if candidate.is_dir():
            trees[candidate] = None
    return list(trees)


def _touches_a_routed_tree(directory: Path, routed: set[Path]) -> bool:
    """Whether a routed page tree is this directory, or sits above or below it.

    A routed tree nested inside the candidate makes the candidate part of a
    served layout, which is the shape of an application package that happens
    to carry the configured `PAGES_DIR` name.
    """
    return any(
        root.is_relative_to(directory) or directory.is_relative_to(root)
        for root in routed
    )


def _holds_a_page(directory: Path) -> bool:
    """Whether the walk finds anything under `directory` the router would route.

    The walk refuses no directory name, because a tree no router routes has
    no skip set of its own.
    """
    return next(walk_page_tree(directory), None) is not None


@register(NEXT)
def check_unrouted_working_directory_pages(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a pages tree beside the process is routed by nobody (`next.W002`).

    A project that leaves `BASE_DIR` unset and lists no root in `DIRS` keeps
    writing pages under a directory the router never reaches, and the pages are
    never served. Nothing else reports that, because the checks walk the trees
    the routers report and this one is not among them.
    """
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []
    routed = {
        root.path.resolve()
        for router in router_manager.backends
        for root in get_page_roots(router)
    }
    return [
        DjangoWarning(
            f"{directory} holds pages that no configured router routes, so "
            "nothing under it is served. Set BASE_DIR in settings so the file "
            "router resolves its root tree, or name the directory in "
            "NEXT_FRAMEWORK['PAGE_BACKENDS'] DIRS.",
            obj=str(directory),
            id="next.W002",
        )
        for directory in _working_directory_page_trees()
        if not _touches_a_routed_tree(directory, routed) and _holds_a_page(directory)
    ]


def _check_directory_syntax(
    directories: list[Path], pages_path: Path, context: str
) -> list[CheckMessage]:
    """Check directory names under `pages_path` for valid bracket syntax."""
    errors: list[CheckMessage] = []

    for item in directories:
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
                    )
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
                    )
                )

        elif dir_name_str.startswith("["):
            errors.append(
                Error(
                    f"{context} pages: Incomplete args syntax "
                    f'"{dir_name_str}" in {relative_path}. '
                    f"Use [[args]] format.",
                    obj=settings,
                    id="next.E009",
                )
            )

    return errors


def _check_missing_page_files(
    directories: list[Path], pages_path: Path, context: str
) -> list[CheckMessage]:
    """Check for missing `page.py` files inside parameter directories."""
    errors: list[CheckMessage] = []

    for item in directories:
        dir_name_str = item.name
        if (dir_name_str.startswith("[") and dir_name_str.endswith("]")) or (
            dir_name_str.startswith("[[") and dir_name_str.endswith("]]")
        ):
            page_file = item / "page.py"
            layout_file = item / "layout.djx"
            template_file = item / "template.djx"

            if page_file.exists() or layout_file.exists() or template_file.exists():
                continue

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
                    )
                )

    return errors


def _check_pages_directory(
    pages_path: Path, context: str, seen: set[Path]
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check a specific pages directory for issues, skipping directories in `seen`."""
    if not pages_path.exists():
        return [], []

    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    directories = [
        item
        for item in pages_path.rglob("*")
        if item.is_dir() and first_visit(item, seen)
    ]
    errors.extend(_check_directory_syntax(directories, pages_path, context))
    errors.extend(_check_missing_page_files(directories, pages_path, context))

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


@register(NEXT)
def check_page_functions(*args, **kwargs) -> list[CheckMessage]:
    """Validate each page module for `render` or `template`. Warn when empty."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    # One `page.py` reached through several page trees is one page.
    seen: set[Path] = set()
    for router in router_manager.backends:
        try:
            for root in get_page_roots(router):
                root_errors, root_warnings = _check_page_functions_in_directory(
                    root.path, root.label, seen
                )
                errors.extend(root_errors)
                warnings.extend(root_warnings)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"Error checking page functions: {e}", obj=settings, id="next.E011"
                )
            )

    return errors + warnings


def _check_page_functions_in_directory(
    pages_path: Path, context: str, seen: set[Path]
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check `page.py` files for render/template rules, skipping files in `seen`."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    if not pages_path.exists():
        return errors, warnings

    for page_file in pages_path.rglob("page.py"):
        if not first_visit(page_file, seen):
            continue
        render_func = _load_render_function(page_file)
        if last_load_error(page_file) is not None:
            # A broken import surfaces once through next.E017, so the
            # body-source checks stay silent for this file.
            continue
        has_template = _has_template_or_djx(page_file)
        hard_error = False

        if render_func is None and not has_template:
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    "has no body source. Add a render function, a template "
                    "attribute, a sibling template.djx, or a sibling layout.djx.",
                    obj=settings,
                    id="next.E012",
                )
            )
            hard_error = True
        elif render_func is not None and not callable(render_func):
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    f"render attribute is not callable.",
                    obj=settings,
                    id="next.E013",
                )
            )
            hard_error = True

        if not hard_error:
            shadow_warning = _check_body_source_conflicts(page_file)
            if shadow_warning is not None:
                warnings.append(shadow_warning)

    return errors, warnings


def _active_body_sources(page_file: Path) -> list[str]:
    """Return the body sources declared on `page_file` in priority order.

    The priority order starts with `render()`, then the `template`
    module attribute, and finally registered loaders in the order
    declared under `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`. Each loader
    reports its file name via `TemplateLoader.source_name`.
    """
    module = _load_python_module_memo(page_file)
    sources: list[str] = []
    if module is not None:
        if callable(getattr(module, "render", None)):
            sources.append("render()")
        template_attr = getattr(module, "template", None)
        if isinstance(template_attr, str):
            sources.append("template")
    sources.extend(
        loader.source_name
        for loader in build_registered_loaders()
        if loader.can_load(page_file) and loader.source_name
    )
    return sources


def _check_body_source_conflicts(page_file: Path) -> CheckMessage | None:
    """Warn (`next.W043`) when more than one body source is declared for `page_file`."""
    sources = _active_body_sources(page_file)
    if len(sources) < _MIN_CONFLICTING_BODY_SOURCES:
        return None
    winner = sources[0]
    shadowed = ", ".join(sources[1:])
    return DjangoWarning(
        f"{page_file} declares multiple body sources: {', '.join(sources)}. "
        f"{winner} takes priority and {shadowed} will not be used. "
        "Priority order: render() > template > registered TEMPLATE_LOADERS.",
        obj=str(page_file),
        id="next.W043",
    )


def _load_render_function(file_path: Path) -> object:
    """Return the `render` callable declared in a `page.py`, or `None`.

    A broken `page.py` loads as `None` and yields `None` here. The caller
    reads `last_load_error` and leaves the failure to `next.E017`.
    """
    module = _load_python_module_memo(file_path)
    if module is None:
        return None
    return getattr(module, "render", None)


def _has_template_or_djx(file_path: Path) -> bool:
    """Return True when the page has a body source or a sibling ``layout.djx``."""
    if (file_path.parent / "layout.djx").exists():
        return True

    module = _load_python_module_memo(file_path)
    if module is not None and hasattr(module, "template"):
        return True

    return any(loader.can_load(file_path) for loader in build_registered_loaders())


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


@register(Tags.templates, NEXT)
def check_layout_templates(*args, **kwargs) -> list[CheckMessage]:
    """Check `layout.djx` files for the `{% block template %}` structure."""
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors + warnings

    # Nested roots and several routers reach the same layout through more
    # than one page.
    seen: set[Path] = set()
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            layout_file = page_path.parent / "layout.djx"
            if not layout_file.exists() or not first_visit(layout_file, seen):
                continue

            warning = _check_layout_file(layout_file)
            if warning:
                warnings.append(warning)

    return warnings


_DICT_ANNOTATION_NAMES = frozenset({"dict", "Dict", "Mapping", "MutableMapping"})


def _annotation_is_dict_like(annotation: object) -> bool:
    """Return True when the return annotation maps to a dict-like result."""
    if annotation is inspect.Signature.empty:
        return True
    if annotation is dict or annotation is None:
        return annotation is dict
    origin = get_origin(annotation)
    if origin is not None:
        candidate: object = origin
    else:
        candidate = annotation
    if isinstance(candidate, type):
        try:
            return issubclass(candidate, Mapping)
        except TypeError:
            return False
    name = getattr(candidate, "_name", None) or getattr(candidate, "__name__", None)
    if isinstance(name, str):
        return name in _DICT_ANNOTATION_NAMES
    return False


def _check_context_function(
    func_name: str, func: Callable[..., Any], page_path: Path
) -> CheckMessage | None:
    """Emit an error when keyless context callables are not annotated dict-like.

    The check is static: executing user code at ``manage.py check`` time
    is expensive and can hit databases that have not been migrated yet.
    Callables without a return annotation are accepted — the runtime
    emits a clear ``TypeError`` on first render if the result is not a
    mapping.
    """
    try:
        annotation = inspect.signature(func).return_annotation
    except (TypeError, ValueError):
        return None
    if _annotation_is_dict_like(annotation):
        return None
    annotation_name = getattr(annotation, "__name__", None) or repr(annotation)
    return Error(
        f"Context function '{func_name}' in {page_path} "
        "must return a dictionary when registered as a keyless context "
        f"(got return annotation {annotation_name}). "
        "Annotate it '-> dict' (or a TypedDict), or register it with a key "
        "like @context('name').",
        obj=str(page_path),
        id="next.E029",
    )


def _check_registered_context_functions(page_path: Path) -> list[CheckMessage]:
    """Return keyless `@context` errors recorded for `page_path` in the registry.

    The registry keys on the file declaring the callable, which for a `page.py`
    is the absolute path importlib gave the module, the same path this check
    loads it by, so a direct lookup needs no symlink resolution on either side.
    """
    errors: list[CheckMessage] = []
    registry = page._context_manager._context_registry.get(page_path, {})
    for key, entry in registry.items():
        if key is not None:
            continue
        error = _check_context_function(
            callable_name(entry.func), entry.func, page_path
        )
        if error is not None:
            errors.append(error)
    return errors


def _iter_existing_scanned_pages(
    router_manager: RouterManager, seen: set[Path]
) -> Iterator[Path]:
    """Yield each existing `page.py` once across routers, de-duplicated by `seen`.

    The identity is the resolved path, so a tree reached through a symlink
    reports once. The spelling the router walked is what travels on, because
    the loader and the page-context registry both key on that spelling.
    Virtual `template.djx`-only pages carry a non-existent path and are skipped.
    """
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            if not first_visit(page_path, seen):
                continue
            if page_path.exists():
                yield page_path


def _page_import_error_message(page_path: Path) -> str:
    """Compose the `next.E017` text, naming the recorded failure when known."""
    error = last_load_error(page_path)
    if error is None:
        return (
            f"page.py at {page_path} could not be imported. Fix the syntax or "
            "import error so the framework stops skipping the module silently."
        )
    cause = error.__cause__
    return (
        f"page.py at {page_path} failed to import "
        f"({type(cause).__name__}: {cause}). A raising import in the module "
        "body counts the same as a syntax error. Fix it so the framework "
        "stops skipping the module silently."
    )


@register(Tags.templates, NEXT)
def check_page_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Report `page.py` files that raise while importing (`next.E017`).

    The message carries the recorded cause, so an ImportError raised by
    the module body is named as such instead of masking as a missing body.
    """
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors
    return [
        Error(_page_import_error_message(page_path), obj=str(page_path), id="next.E017")
        for page_path in _iter_existing_scanned_pages(router_manager, set())
        if _load_python_module_memo(page_path) is None
    ]


@register(Tags.templates, NEXT)
def check_context_functions(*args, **kwargs) -> list[CheckMessage]:
    """Require keyless `@context` callables to return a dict when invoked."""
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    errors: list[CheckMessage] = []
    for page_path in _iter_existing_scanned_pages(router_manager, set()):
        if _load_python_module_memo(page_path) is None:
            continue
        errors.extend(_check_registered_context_functions(page_path))
    return errors


@register(Tags.templates, NEXT)
def check_context_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@context` no page render collects (`next.E074`).

    A registration keys on the file declaring the callable, so decorating an
    imported helper binds it to that helper's module, and decorating a
    callable from a sibling `page.py` binds it to that other page.
    """
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    for page_path in _iter_existing_scanned_pages(router_manager, set()):
        _load_python_module_memo(page_path)

    return registration_file_errors(
        _PAGE_CONTEXT_SUBJECT,
        registrations=page._context_manager.registered_names(),
        misattributed=page._context_manager.misattributed(),
    )


@register(Tags.templates, NEXT)
def check_single_keyless_context(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `page.py` with more than one keyless `@context` (`next.E018`).

    Keyless callables share one slot, so only the last survives and runs.
    """
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    errors: list[CheckMessage] = []
    conflicts = page._context_manager._keyless_conflicts
    for page_path in _iter_existing_scanned_pages(router_manager, set()):
        if _load_python_module_memo(page_path) is None:
            continue
        names = conflicts.get(page_path)
        if names:
            joined = ", ".join(names)
            errors.append(
                Error(
                    f"page.py at {page_path} registers multiple keyless @context "
                    f"callables ({joined}). Only the last one runs, so the "
                    "earlier ones are ignored. Give each a key like "
                    "@context('name'), or merge them into a single callable.",
                    obj=str(page_path),
                    id="next.E018",
                )
            )
    return errors


@register(Tags.templates, NEXT)
def check_context_processor_signature(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a configured context processor has no `request` parameter."""
    errors: list[CheckMessage] = []
    for backend_index, backend in _iter_page_backend_configs():
        processors = backend.get("OPTIONS", {}).get("context_processors") or []
        for processor_index, path in enumerate(processors):
            if not isinstance(path, str):
                continue
            loc = (
                f"NEXT_FRAMEWORK['PAGE_BACKENDS'][{backend_index}]"
                f".OPTIONS.context_processors[{processor_index}]"
            )
            message = _check_processor_request_parameter(path, loc)
            if message is not None:
                errors.append(message)
    return errors


def _iter_page_backend_configs() -> list[tuple[int, dict[str, Any]]]:
    """Return indexed page backend dicts from `NEXT_FRAMEWORK`."""
    raw = getattr(settings, "NEXT_FRAMEWORK", {}) or {}
    backends = raw.get("PAGE_BACKENDS", []) if isinstance(raw, dict) else []
    return [
        (idx, backend)
        for idx, backend in enumerate(backends)
        if isinstance(backend, dict)
    ]


def _check_processor_request_parameter(
    processor_path: str, location: str
) -> CheckMessage | None:
    """Return an error when the callable at `processor_path` lacks `request`."""
    try:
        processor = importlib.import_module(processor_path.rsplit(".", 1)[0])
    except (ImportError, ValueError):
        return None
    attr_name = processor_path.rsplit(".", 1)[-1]
    callable_obj = getattr(processor, attr_name, None)
    if not callable(callable_obj):
        return None
    try:
        sig = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return None
    if "request" in sig.parameters:
        return None
    return Error(
        f"{location} points at {processor_path!r} which does not accept a "
        "'request' parameter. Context processors must accept request.",
        obj=settings,
        id="next.E040",
    )


@register(NEXT)
def check_template_loaders(*args, **kwargs) -> list[CheckMessage]:
    """Validate every `NEXT_FRAMEWORK['TEMPLATE_LOADERS']` entry."""
    try:
        configured = next_framework_settings.TEMPLATE_LOADERS
    except (AttributeError, ImportError):  # pragma: no cover
        return []

    messages: list[CheckMessage] = []
    for index, entry in enumerate(configured):
        if not isinstance(entry, str):
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK['TEMPLATE_LOADERS'][{index}] must be a dotted "
                    f"path string, got {type(entry).__name__!r}.",
                    obj=settings,
                    id="next.E042",
                )
            )
            continue
        try:
            cls = import_class_cached(entry)
        except ImportError as exc:
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK['TEMPLATE_LOADERS'][{index}]={entry!r} "
                    f"cannot be imported: {exc}.",
                    obj=settings,
                    id="next.E043",
                )
            )
            continue
        if not isinstance(cls, type) or not issubclass(cls, TemplateLoader):
            messages.append(
                Error(
                    f"NEXT_FRAMEWORK['TEMPLATE_LOADERS'][{index}]={entry!r} is "
                    "not a TemplateLoader subclass.",
                    obj=settings,
                    id="next.E043",
                )
            )
    return messages


__all__ = [
    "check_context_functions",
    "check_context_processor_signature",
    "check_context_registration_files",
    "check_layout_templates",
    "check_page_functions",
    "check_page_module_imports",
    "check_pages_structure",
    "check_request_in_context",
    "check_single_keyless_context",
    "check_template_loaders",
    "check_unrouted_working_directory_pages",
]
