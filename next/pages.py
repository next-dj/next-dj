"""File-based page rendering system for Django applications.

This module implements a sophisticated page rendering system that automatically
generates Django views and URL patterns from page.py files located in application
directories. The system supports multiple template sources, context management,
and seamless integration with Django's URL routing.

The core concept is simple: place a page.py file in any directory within your
Django app's pages/ folder, and the system will automatically create a corresponding
URL pattern and view function. Pages can define templates either as Python string
attributes or as separate .djx files, providing flexibility in template management.

The system uses a plugin architecture with template loaders that can be extended
to support different template sources. Context functions can be registered to
provide data to templates, and the entire system is designed to be performant
with caching and lazy loading throughout.
"""

import contextlib
import importlib.util
import inspect
import logging
import types
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template import Context, Template
from django.urls import URLPattern, path
from django.utils.module_loading import import_string


if TYPE_CHECKING:  # pragma: no cover
    from .urls import URLPatternParser


# URL pattern naming template
URL_NAME_TEMPLATE = "page_{name}"


logger = logging.getLogger(__name__)


def _import_context_processor(
    processor_path: str,
) -> Callable[[Any], dict[str, Any]] | None:
    """Import a single context processor by path."""
    try:
        processor = import_string(processor_path)
        # type check to ensure it's a callable
        if callable(processor):
            return processor  # type: ignore[no-any-return]
    except (ImportError, AttributeError) as e:
        logger.warning("Could not import context processor %s: %s", processor_path, e)
    return None


def _extract_processor_paths(configs: list[dict]) -> list[str]:
    """Extract context processor paths from NEXT_PAGES configurations.

    Scans all configurations for context_processors in OPTIONS and returns a flat list
    of processor paths.
    """
    processor_paths = []

    for config in configs:
        if not isinstance(config, dict) or "OPTIONS" not in config:
            continue

        options = config["OPTIONS"]
        if "context_processors" in options and isinstance(
            options["context_processors"],
            list,
        ):
            processor_paths.extend(options["context_processors"])

    return processor_paths


def _get_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Load context processors from NEXT_PAGES configuration.

    Retrieves context processors from NEXT_PAGES.OPTIONS.context_processors
    setting, similar to how Django handles TEMPLATES context_processors.
    Returns a list of callable context processors.
    """
    # get NEXT_PAGES configuration
    next_pages_config = getattr(settings, "NEXT_PAGES", [])

    # extract all processor paths
    processor_paths = _extract_processor_paths(next_pages_config)

    # import processors and filter out failed imports
    return [
        processor
        for processor_path in processor_paths
        if (processor := _import_context_processor(processor_path))
    ]


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Load Python module from file path, returning None on failure."""
    try:
        spec = importlib.util.spec_from_file_location("page_module", file_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, AttributeError, OSError, SyntaxError):
        return None
    else:
        return module


class TemplateLoader(ABC):
    """Abstract interface for loading page templates from various sources.

    Implements the Strategy pattern to allow different template loading mechanisms
    (Python modules, .djx files, etc.) to be used interchangeably. Each loader
    is responsible for detecting whether it can handle a specific file and
    extracting template content from it.
    """

    @abstractmethod
    def can_load(self, file_path: Path) -> bool:
        """Determine if this loader can extract a template from the given file.

        Performs lightweight checks (file existence, basic validation) without
        expensive operations like full module loading or file reading.
        """

    @abstractmethod
    def load_template(self, file_path: Path) -> str | None:
        """Extract template content from the file, returning None on failure.

        Performs the actual template extraction. Should handle errors gracefully
        and return None rather than raising exceptions for recoverable failures.
        """


class PythonTemplateLoader(TemplateLoader):
    """Loads templates from Python modules that define a 'template' attribute.

    This loader handles the traditional approach where page.py files contain
    a module-level 'template' string variable. It dynamically imports the
    module and extracts the template content, making it suitable for simple
    page definitions without complex logic.
    """

    def can_load(self, file_path: Path) -> bool:
        """Check if the Python module contains a 'template' attribute."""
        module = _load_python_module(file_path)
        return module is not None and hasattr(module, "template")

    def load_template(self, file_path: Path) -> str | None:
        """Extract the template string from the module's 'template' attribute."""
        module = _load_python_module(file_path)
        return getattr(module, "template", None) if module else None


class DjxTemplateLoader(TemplateLoader):
    """Loads templates from .djx files located alongside page.py files.

    This loader implements the alternative template approach where page.py
    files without a 'template' attribute are paired with a corresponding
    template.djx file containing the HTML template. This separation allows
    for cleaner code organization and better template editing experience.
    """

    def can_load(self, file_path: Path) -> bool:
        """Check if a corresponding template.djx file exists."""
        return (file_path.parent / "template.djx").exists()

    def load_template(self, file_path: Path) -> str | None:
        """Read and return the content of the template.djx file."""
        djx_file = file_path.parent / "template.djx"
        try:
            return djx_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


class LayoutTemplateLoader(TemplateLoader):
    """Loads layout templates from layout.djx files in parent directories.

    This loader implements the layout inheritance system by scanning
    parent directories for layout.djx files. It supports hierarchical
    layout inheritance where templates can extend layouts from their
    parent directories, creating a nested inheritance chain.
    """

    def can_load(self, file_path: Path) -> bool:
        """Check if any layout.djx file exists in the directory hierarchy.

        Walks up the directory tree from the template's location to
        find layout.djx files. Returns True if at least one layout
        is found in the hierarchy.
        """
        return self._find_layout_files(file_path) is not None

    def load_template(self, file_path: Path) -> str | None:
        """Load and compose layout templates from the directory hierarchy.

        Discovers all layout.djx files in the parent directories and
        composes them into a single template using manual string replacement.
        """
        layout_files = self._find_layout_files(file_path)
        if not layout_files:
            return None

        # wrap template content in template block
        template_content = self._wrap_in_template_block(file_path)

        # compose layout hierarchy
        return self._compose_layout_hierarchy(template_content, layout_files)

    def _find_layout_files(self, file_path: Path) -> list[Path] | None:
        """Find all layout.djx files in the directory hierarchy.

        Walks up the directory tree from the template's location
        and collects all layout.djx files found. Returns them in
        order from closest to furthest parent directory.
        """
        layout_files = []
        current_dir = file_path.parent

        # check current directory first, then walk up the directory tree
        while current_dir != current_dir.parent:  # not at root
            layout_file = current_dir / "layout.djx"
            if layout_file.exists():
                layout_files.append(layout_file)
            current_dir = current_dir.parent

        # also add additional layouts from other NEXT_PAGES directories
        # but only if they're not already in the local hierarchy
        if additional_layouts := self._get_additional_layout_files():
            for additional_layout in additional_layouts:
                if additional_layout not in layout_files:
                    layout_files.append(additional_layout)

        return layout_files or None

    def _get_additional_layout_files(self) -> list[Path]:
        """Get layout.djx files from other NEXT_PAGES directories.

        Scans all configured NEXT_PAGES directories for layout.djx files
        that should be available for inheritance across different apps.
        """
        additional_layouts = []
        next_pages_config = getattr(settings, "NEXT_PAGES", [])

        for config in next_pages_config:
            if not isinstance(config, dict):
                continue

            pages_dir = self._get_pages_dir_for_config(config)
            if not pages_dir or not pages_dir.exists():
                continue

            layout_file = pages_dir / "layout.djx"
            if layout_file.exists() and layout_file not in additional_layouts:
                additional_layouts.append(layout_file)

        return additional_layouts

    def _get_pages_dir_for_config(self, config: dict) -> Path | None:
        """Get the pages directory path for a NEXT_PAGES configuration."""
        if config.get("APP_DIRS", True):
            # for app directories, we can't easily determine the path here
            # this will be handled by the individual app scanning
            return None

        options = config.get("OPTIONS", {})
        if "PAGES_DIR" in options:
            return Path(options["PAGES_DIR"])

        return None

    def _wrap_in_template_block(self, file_path: Path) -> str:
        """Wrap template content in a template block for inheritance.

        Reads the template file and wraps its content in Django's
        template block syntax to enable proper inheritance from
        layout templates. If there's a layout.djx file in the same
        directory as the template, the template is returned as-is
        since it's already wrapped in the layout.
        """
        template_file = file_path.parent / "template.djx"
        if template_file.exists():
            with contextlib.suppress(OSError, UnicodeDecodeError):
                content = template_file.read_text(encoding="utf-8")
                # check if there's a layout file in the same directory
                layout_file = file_path.parent / "layout.djx"
                if layout_file.exists():
                    # template is already wrapped in layout, return as-is
                    return content
                return f"{{% block template %}}{content}{{% endblock template %}}"
        return "{% block template %}{% endblock template %}"

    def _compose_layout_hierarchy(
        self,
        template_content: str,
        layout_files: list[Path],
    ) -> str:
        """Compose layout hierarchy by nesting layouts and inserting content.

        Processes layout files in order, with local layouts taking precedence
        over additional layouts from other NEXT_PAGES directories.
        """
        result = template_content

        # process all layout files in order (local layouts come first due to
        # how _find_layout_files builds the list)
        for layout_file in layout_files:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                layout_content = layout_file.read_text(encoding="utf-8")
                result = layout_content.replace(
                    "{% block template %}{% endblock template %}",
                    result,
                )
        return result


class LayoutManager:
    """Manages layout template discovery and inheritance for page templates.

    This class implements a sophisticated layout inheritance system that
    automatically discovers layout.djx files in the directory hierarchy
    and composes them using Django's template inheritance mechanism.
    The system supports nested layouts where templates can inherit from
    multiple levels of parent directories.

    The manager maintains a registry of discovered layouts and provides
    efficient lookup and composition services for the template rendering
    system.
    """

    def __init__(self) -> None:
        """Initialize the layout manager with empty registry."""
        self._layout_registry: dict[Path, str] = {}
        self._layout_loader = LayoutTemplateLoader()

    def discover_layouts_for_template(self, template_path: Path) -> str | None:
        """Discover and compose layout hierarchy for a template.

        Scans the directory hierarchy for layout.djx files and composes
        them into a single template using Django's extends mechanism.
        Returns the composed template or None if no layouts are found.
        """
        if not self._layout_loader.can_load(template_path):
            return None

        composed_template = self._layout_loader.load_template(template_path)
        if composed_template:
            self._layout_registry[template_path] = composed_template

        return composed_template

    def get_layout_template(self, template_path: Path) -> str | None:
        """Get the composed layout template for a given template path.

        Returns the previously discovered and composed layout template
        or None if no layout has been discovered for this path.
        """
        return self._layout_registry.get(template_path)

    def clear_registry(self) -> None:
        """Clear the layout registry to free memory."""
        self._layout_registry.clear()


class ContextManager:
    """Manages context functions and their execution for page templates.

    Implements a registry system that maps file paths to context functions,
    allowing each page to have its own set of context providers. Supports
    two registration patterns: keyed functions (returning single values)
    and unkeyed functions (returning dictionaries that get merged).

    Also supports context inheritance from layout directories where
    context functions marked with inherit_context=True will be available
    to child pages using layout.djx files.
    """

    def __init__(self) -> None:
        """Initialize the context manager with empty registry."""
        self._context_registry: dict[
            Path,
            dict[str | None, tuple[Callable[..., Any], bool]],
        ] = {}

    def register_context(
        self,
        file_path: Path,
        key: str | None,
        func: Callable[..., Any],
        *,
        inherit_context: bool = False,
    ) -> None:
        """Register a context function for a specific file.

        Associates a callable with a file path and optional key. Keyed functions
        are stored under their key name, while unkeyed functions (key=None) are
        expected to return dictionaries that will be merged into the context.
        """
        self._context_registry.setdefault(file_path, {})[key] = (func, inherit_context)

    def collect_context(
        self,
        file_path: Path,
        *args: object,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Execute all registered context functions for a file and merge results.

        Runs all context functions associated with the file, passing through
        any provided arguments. Keyed functions contribute single values,
        while unkeyed functions contribute entire dictionaries that get merged.
        Returns the combined context data for template rendering.
        """
        context_data = {}

        # collect inherited context from layout directories first (lower priority)
        inherited_context = self._collect_inherited_context(file_path, *args, **kwargs)
        context_data.update(inherited_context)

        # collect context from the current file
        # (higher priority - can override inherited)
        for key, (func, _) in self._context_registry.get(file_path, {}).items():
            if key is None:
                context_data.update(func(*args, **kwargs))
            else:
                context_data[key] = func(*args, **kwargs)

        return context_data

    def _collect_inherited_context(
        self,
        file_path: Path,
        *args: object,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Collect context from layout directories that should be inherited.

        Walks up the directory tree from the template's location to find
        layout.djx files and collects context from their corresponding
        page.py files if they have inherit_context=True functions.
        """
        inherited_context = {}
        current_dir = file_path.parent

        # walk up the directory tree to find layout directories
        while current_dir != current_dir.parent:  # not at root
            layout_file = current_dir / "layout.djx"
            page_file = current_dir / "page.py"

            # if layout.djx exists, check for page.py with inheritable context
            if layout_file.exists() and page_file.exists():
                for key, (func, inherit_context) in self._context_registry.get(
                    page_file,
                    {},
                ).items():
                    if inherit_context:
                        if key is None:
                            inherited_context.update(func(*args, **kwargs))
                        else:
                            inherited_context[key] = func(*args, **kwargs)

            current_dir = current_dir.parent

        return inherited_context


class Page:
    """Central coordinator for page-based template rendering and URL pattern generation.

    Acts as the main facade that orchestrates template loading, context management,
    and URL pattern creation. Implements a plugin architecture where different
    template loaders can be registered and tried in sequence. Manages the
    complete lifecycle from page file discovery to Django URL pattern generation.
    """

    def __init__(self) -> None:
        """Initialize the page manager with empty registries."""
        self._template_registry: dict[Path, str] = {}
        self._context_manager = ContextManager()
        self._layout_manager = LayoutManager()
        self._template_loaders = [
            PythonTemplateLoader(),
            DjxTemplateLoader(),
            LayoutTemplateLoader(),
        ]

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Manually register a template string for a specific file path.

        This method is typically called internally by template loaders after
        successful template extraction. Stores the template content for later
        rendering, with file path as the key for efficient lookup.
        """
        self._template_registry[file_path] = template_str

    def _get_caller_path(self, back_count: int = 1) -> Path:
        """Extract the file path of the calling code using stack frame inspection.

        Walks up the call stack to find the actual module file that contains
        the calling code, skipping over this module itself. Used primarily
        by the context decorator to automatically associate context functions
        with their source files without requiring manual path specification.
        """
        frame = inspect.currentframe()
        for _ in range(back_count):
            if not frame or not frame.f_back:
                msg = "Could not determine caller file path"
                raise RuntimeError(msg)
            frame = frame.f_back

        # skip over this module to find the actual caller
        for _ in range(10):  # Prevent infinite loops
            if not frame:
                break
            file_path = frame.f_globals.get("__file__")
            if file_path and not file_path.endswith("pages.py"):
                return Path(file_path)
            frame = frame.f_back

        msg = "Could not determine caller file path"
        raise RuntimeError(msg)

    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        inherit_context: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register context functions that provide template variables.

        Supports two usage patterns:
        1. @context("key") - function result stored under the specified key
        2. @context - function must return a dictionary that gets merged

        Automatically detects the calling file and associates the context function
        with it. The function will be called during template rendering with the
        same arguments passed to the render method.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                # @context usage - function returns dict
                caller_path = self._get_caller_path(2)
                self._context_manager.register_context(
                    caller_path,
                    None,
                    func_or_key,
                    inherit_context=inherit_context,
                )
            else:
                # @context("key") usage - function result stored under key
                caller_path = self._get_caller_path(1)
                self._context_manager.register_context(
                    caller_path,
                    func_or_key,
                    func,
                    inherit_context=inherit_context,
                )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def render(self, file_path: Path, *args: object, **kwargs: object) -> str:
        """Render a template with context data and return the final HTML.

        Combines template content with context data from registered functions
        and any additional variables passed as kwargs. Template variables
        take precedence over context function results. Uses Django's
        template engine for rendering with full tag and filter support.

        Supports context_processors from NEXT_PAGES.OPTIONS.context_processors.
        """
        template_str = self._template_registry[file_path]

        # create default context that's always available
        context_data = {}
        # add kwargs first (lower priority)
        context_data.update(kwargs)
        # add context functions (higher priority - can override kwargs)
        context_data.update(
            self._context_manager.collect_context(file_path, *args, **kwargs),
        )

        # check if we have a request object for context_processors
        request = None
        if args and isinstance(args[0], HttpRequest):  # first arg is likely a request
            request = args[0]

        # add context_processors data if we have a request and context_processors
        context_processors = _get_context_processors()
        if request and context_processors:
            # manually add context_processors data
            for processor in context_processors:
                try:
                    processor_data = processor(request)
                    if isinstance(processor_data, dict):
                        context_data.update(processor_data)
                except (TypeError, ValueError, AttributeError, KeyError) as e:
                    logger.warning(
                        "Error in context processor %s: %s",
                        processor.__name__,
                        e,
                    )

        return Template(template_str).render(Context(context_data))

    def _create_view_function(
        self,
        file_path: Path,
        parameters: dict[str, str],
    ) -> Callable[..., HttpResponse]:
        """Create a view function that handles URL parameters and template rendering."""

        def view(request: HttpRequest, **kwargs: object) -> HttpResponse:
            kwargs.update(parameters)
            return HttpResponse(self.render(file_path, request, **kwargs))

        return view

    def _load_template_for_file(self, file_path: Path) -> bool:
        """Load template content for a file using available template loaders."""
        # try layout template loader first (priority for layout inheritance)
        if self._layout_manager.discover_layouts_for_template(file_path):
            layout_template = self._layout_manager.get_layout_template(file_path)
            if layout_template:
                self.register_template(file_path, layout_template)
                return True

        # try regular template loaders
        for loader in self._template_loaders:
            if isinstance(loader, LayoutTemplateLoader):
                continue  # already handled above
            if loader.can_load(file_path):
                template_content = loader.load_template(file_path)
                if template_content:
                    self.register_template(file_path, template_content)
                    return True
        return False

    def _create_url_pattern_with_view(
        self,
        django_pattern: str,
        view: Callable,
        clean_name: str,
    ) -> URLPattern:
        """Create a URL pattern with the given view function."""
        return path(
            django_pattern,
            view,
            name=URL_NAME_TEMPLATE.format(name=clean_name),
        )

    def _create_regular_page_pattern(
        self,
        file_path: Path,
        django_pattern: str,
        parameters: dict[str, str],
        clean_name: str,
    ) -> URLPattern | None:
        """Create URL pattern for a regular page with page.py file.

        Handles template loading and custom render function fallback.
        """
        module = _load_python_module(file_path)
        if not module:
            return None

        # try template-based rendering first
        if self._load_template_for_file(file_path):
            view = self._create_view_function(file_path, parameters)
            return self._create_url_pattern_with_view(django_pattern, view, clean_name)

        # fall back to custom render function
        if (render_func := getattr(module, "render", None)) and callable(render_func):
            return self._create_url_pattern_with_view(
                django_pattern,
                render_func,
                clean_name,
            )

        return None

    def _create_virtual_page_pattern(
        self,
        file_path: Path,
        django_pattern: str,
        parameters: dict[str, str],
        clean_name: str,
    ) -> URLPattern | None:
        """Create URL pattern for a virtual page with template.djx but no page.py.

        Handles template-only rendering for virtual views.
        """
        if self._load_template_for_file(file_path):
            view = self._create_view_function(file_path, parameters)
            return self._create_url_pattern_with_view(django_pattern, view, clean_name)
        return None

    def create_url_pattern(
        self,
        url_path: str,
        file_path: Path,
        url_parser: "URLPatternParser",
    ) -> URLPattern | None:
        """Generate a Django URL pattern from a page file with template detection.

        Processes the page file to create a complete Django URL pattern. First attempts
        to load a template using registered loaders, falling back to a custom render
        function if available. Creates a view function that handles URL parameters
        and template rendering automatically.

        If no page.py exists but template.djx is present, creates a virtual view
        that renders the template directly.
        """
        django_pattern, parameters = url_parser.parse_url_pattern(url_path)
        clean_name = url_parser.prepare_url_name(url_path)

        if file_path.exists():
            return self._create_regular_page_pattern(
                file_path,
                django_pattern,
                parameters,
                clean_name,
            )
        return self._create_virtual_page_pattern(
            file_path,
            django_pattern,
            parameters,
            clean_name,
        )


# global singleton instance for application-wide page management
page = Page()

# convenience alias for the context decorator
context = page.context
