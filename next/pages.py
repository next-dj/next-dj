"""
File-based page rendering system for Django applications.

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

import importlib.util
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.http import HttpResponse
from django.template import Context, Template
from django.urls import URLPattern, path

# URL pattern naming template
URL_NAME_TEMPLATE = "page_{name}"


class TemplateLoader(ABC):
    """
    Abstract interface for loading page templates from various sources.

    Implements the Strategy pattern to allow different template loading mechanisms
    (Python modules, .djx files, etc.) to be used interchangeably. Each loader
    is responsible for detecting whether it can handle a specific file and
    extracting template content from it.
    """

    @abstractmethod
    def can_load(self, file_path: Path) -> bool:
        """
        Determine if this loader can extract a template from the given file.

        Performs lightweight checks (file existence, basic validation) without
        expensive operations like full module loading or file reading.
        """
        pass

    @abstractmethod
    def load_template(self, file_path: Path) -> str | None:
        """
        Extract template content from the file, returning None on failure.

        Performs the actual template extraction. Should handle errors gracefully
        and return None rather than raising exceptions for recoverable failures.
        """
        pass


class PythonTemplateLoader(TemplateLoader):
    """
    Loads templates from Python modules that define a 'template' attribute.

    This loader handles the traditional approach where page.py files contain
    a module-level 'template' string variable. It dynamically imports the
    module and extracts the template content, making it suitable for simple
    page definitions without complex logic.
    """

    def can_load(self, file_path: Path) -> bool:
        """
        Check if the Python module contains a 'template' attribute.

        Performs a lightweight check by importing the module and checking
        for the presence of the template attribute without executing the
        module's code unnecessarily.
        """
        try:
            spec = importlib.util.spec_from_file_location("page_module", file_path)
            if not spec or not spec.loader:
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return hasattr(module, "template")
        except Exception:
            return False

    def load_template(self, file_path: Path) -> str | None:
        """
        Extract the template string from the module's 'template' attribute.

        Dynamically imports the module and returns the value of the 'template'
        attribute. Returns None if the module cannot be loaded or doesn't
        contain the expected attribute.
        """
        try:
            spec = importlib.util.spec_from_file_location("page_module", file_path)
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return getattr(module, "template", None)
        except Exception:
            return None


class DjxTemplateLoader(TemplateLoader):
    """
    Loads templates from .djx files located alongside page.py files.

    This loader implements the alternative template approach where page.py
    files without a 'template' attribute are paired with a corresponding
    template.djx file containing the HTML template. This separation allows
    for cleaner code organization and better template editing experience.
    """

    def can_load(self, file_path: Path) -> bool:
        """
        Check if a corresponding template.djx file exists in the same directory.

        Performs a simple file existence check without reading the file
        content, making it very fast for the initial detection phase.
        """
        return (file_path.parent / "template.djx").exists()

    def load_template(self, file_path: Path) -> str | None:
        """
        Read and return the content of the template.djx file.

        Attempts to read the .djx file as UTF-8 text. Returns None if the
        file cannot be read or contains invalid encoding, allowing the
        system to fall back to other template sources gracefully.
        """
        djx_file = file_path.parent / "template.djx"
        try:
            return djx_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


class ContextManager:
    """
    Manages context functions and their execution for page templates.

    Implements a registry system that maps file paths to context functions,
    allowing each page to have its own set of context providers. Supports
    two registration patterns: keyed functions (returning single values)
    and unkeyed functions (returning dictionaries that get merged).
    """

    def __init__(self) -> None:
        self._context_registry: dict[Path, dict[str | None, Callable[..., Any]]] = {}

    def register_context(
        self, file_path: Path, key: str | None, func: Callable[..., Any]
    ) -> None:
        """
        Register a context function for a specific file.

        Associates a callable with a file path and optional key. Keyed functions
        are stored under their key name, while unkeyed functions (key=None) are
        expected to return dictionaries that will be merged into the context.
        """
        self._context_registry.setdefault(file_path, {})[key] = func

    def collect_context(
        self, file_path: Path, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Collects and merges context data for a template.
        Wraps each registered context function with dependency injection,
        automatically supplying common objects (request, session, user) as needed.
        Returns the combined context for rendering.
        """
        from .dependency_resolver import dependency_resolver
        context_data = {}
        # Lazy dependency cache for efficiency
        dep_cache = {}
        def get_dep(name: str, request: Any = None) -> Any:
            if name not in dep_cache:
                if name == "request":
                    dep_cache[name] = request
                elif name == "session":
                    dep_cache[name] = getattr(request, "session", None) if request else None
                elif name == "user":
                    dep_cache[name] = getattr(request, "user", None) if request else None
                else:
                    dep_cache[name] = None
            return dep_cache[name]
        # Detect request object in args/kwargs
        request = kwargs.get("request")
        if not request and args:
            for arg in args:
                if hasattr(arg, "session") and hasattr(arg, "user"):
                    request = arg
                    break
        # Dependency injection setup: inject request, session, and user (as request.user)
        # This ensures context functions get the actual user value, not the whole request object.
        available_deps = {
            "request": lambda: get_dep("request", request),
            "session": lambda: get_dep("session", request),
            "user": lambda: getattr(request, "user", None) if request else None,
        }
        import inspect
        for key, func in self._context_registry.get(file_path, {}).items():
            wrapped_func = dependency_resolver(func, available_deps)
            sig = inspect.signature(func)
            param_list = list(sig.parameters.values())
            # If the context function expects 'user' and the positional arg is a request, inject request.user
            new_args = list(args)
            for idx, param in enumerate(param_list[:len(args)]):
                if param.name == "user" and hasattr(args[idx], "user"):
                    # Replace the positional request with its .user attribute
                    new_args[idx] = getattr(args[idx], "user", None)
            filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters and k not in [p.name for p in param_list[:len(new_args)]]}
            # This logic ensures that context functions get the actual user value, not the whole request object, when 'user' is expected.
            if key is None:
                context_data.update(wrapped_func(*new_args, **filtered_kwargs))
            else:
                context_data[key] = wrapped_func(*new_args, **filtered_kwargs)
        return context_data


class Page:
    """
    Central coordinator for page-based template rendering and URL pattern generation.

    Acts as the main facade that orchestrates template loading, context management,
    and URL pattern creation. Implements a plugin architecture where different
    template loaders can be registered and tried in sequence. Manages the
    complete lifecycle from page file discovery to Django URL pattern generation.
    """

    def __init__(self) -> None:
        self._template_registry: dict[Path, str] = {}
        self._context_manager = ContextManager()
        self._template_loaders = [PythonTemplateLoader(), DjxTemplateLoader()]

    def register_template(self, file_path: Path, template_str: str) -> None:
        """
        Manually register a template string for a specific file path.

        This method is typically called internally by template loaders after
        successful template extraction. Stores the template content for later
        rendering, with file path as the key for efficient lookup.
        """
        self._template_registry[file_path] = template_str

    def _get_caller_path(self, back_count: int = 1) -> Path:
        """
        Extract the file path of the calling code using stack frame inspection.

        Walks up the call stack to find the actual module file that contains
        the calling code, skipping over this module itself. Used primarily
        by the context decorator to automatically associate context functions
        with their source files without requiring manual path specification.
        """
        frame = inspect.currentframe()
        for _ in range(back_count):
            if not frame or not frame.f_back:
                raise RuntimeError("Could not determine caller file path")
            frame = frame.f_back

        # skip over this module to find the actual caller
        for _ in range(10):  # Prevent infinite loops
            if not frame:
                break
            file_path = frame.f_globals.get("__file__")
            if file_path and not file_path.endswith("pages.py"):
                return Path(file_path)
            frame = frame.f_back

        raise RuntimeError("Could not determine caller file path")

    def context(
        self, func_or_key: Any = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Decorator for registering context functions that provide template variables.

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
                self._context_manager.register_context(caller_path, None, func_or_key)
            else:
                # @context("key") usage - function result stored under key
                caller_path = self._get_caller_path(1)
                self._context_manager.register_context(caller_path, func_or_key, func)
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def render(self, file_path: Path, *args: Any, **kwargs: Any) -> str:
        """
        Renders a template with context and supports DI for user render functions.
        Collects context, merges kwargs, and injects dependencies (request, session, user)
        for user-defined render functions if present. Returns the rendered HTML.
        """
        from .dependency_resolver import dependency_resolver
        template_str = self._template_registry[file_path]
        # Dependency cache for user-facing render DI
        dep_cache = {}
        def get_dep(name: str, request: Any = None) -> Any:
            if name not in dep_cache:
                if name == "request":
                    dep_cache[name] = request
                elif name == "session":
                    dep_cache[name] = getattr(request, "session", None) if request else None
                elif name == "user":
                    dep_cache[name] = getattr(request, "user", None) if request else None
                else:
                    dep_cache[name] = None
            return dep_cache[name]
        request = kwargs.get("request")
        if not request and args:
            for arg in args:
                if hasattr(arg, "session") and hasattr(arg, "user"):
                    request = arg
                    break
        available_deps = {
            "request": lambda: get_dep("request", request),
            "session": lambda: get_dep("session", request),
            "user": lambda: get_dep("user", request),
        }
        # Inject DI for user-provided render function
        if "render" in kwargs and callable(kwargs["render"]):
            user_render = dependency_resolver(kwargs["render"], available_deps)
            kwargs["render"] = user_render
        # Ensure request is always passed as a kwarg if found in args
        if request and "request" not in kwargs:
            kwargs = dict(kwargs)
            kwargs["request"] = request
        context_data = self._context_manager.collect_context(file_path, *args, **kwargs)
        context_data.update(kwargs)  # kwargs override context functions
        return Template(template_str).render(Context(context_data))

    def create_url_pattern(
        self, url_path: str, file_path: Path, url_parser: Any
    ) -> URLPattern | None:
        """
        Generate a Django URL pattern from a page file with automatic template detection.

        Processes the page file to create a complete Django URL pattern. First attempts
        to load a template using registered loaders, falling back to a custom render
        function if available. Creates a view function that handles URL parameters
        and template rendering automatically.
        """
        django_pattern, parameters = url_parser.parse_url_pattern(url_path)
        clean_name = url_parser.prepare_url_name(url_path)

        # load and execute the page module
        spec = importlib.util.spec_from_file_location("page_module", file_path)
        if not spec or not spec.loader:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # create view function that handles URL parameters
        def view(request: Any, **kwargs: Any) -> HttpResponse:
            # Merge parameters from URL parser, but don't overwrite explicit kwargs
            merged_kwargs = dict(parameters)
            merged_kwargs.update(kwargs)
            return HttpResponse(self.render(file_path, request, **merged_kwargs))

        # try to load template using available loaders
        for loader in self._template_loaders:
            if loader.can_load(file_path):
                template_content = loader.load_template(file_path)
                if template_content:
                    self.register_template(file_path, template_content)
                    return path(
                        django_pattern,
                        view,
                        name=URL_NAME_TEMPLATE.format(name=clean_name),
                    )

        # fall back to custom render function if available
        if (render_func := getattr(module, "render", None)) and callable(render_func):
            def fallback_view(request: Any, **kwargs: Any) -> Any:
                merged_kwargs = dict(parameters)
                merged_kwargs.update(kwargs)
                return render_func(request, **merged_kwargs)
            return path(
                django_pattern,
                fallback_view,
                name=URL_NAME_TEMPLATE.format(name=clean_name),
            )

        return None


# global singleton instance for application-wide page management
page = Page()

# convenience alias for the context decorator
context = page.context
