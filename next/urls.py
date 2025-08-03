"""
File-based router for Django applications.

Automatically generates URL patterns from page.py files in pages/ directories
of Django applications.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path


class FileRouter:
    """
    File-based router that automatically generates URL patterns from page.py files.

    Scans for pages/ directories in Django applications and creates URL patterns
    based on the directory structure.
    """

    def __init__(self) -> None:
        self.patterns_cache: dict[str, list[URLPattern | URLResolver]] = {}
        # [param] or [type:param]
        self.param_pattern = re.compile(r"\[([^\[\]]+)\]")  # [param] or [int:param]
        self.args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")  # [[args]]

    def get_app_pages_path(self, app_name: str) -> Path | None:
        """Get the pages/ directory path for a Django app."""
        try:
            app_config = settings.INSTALLED_APPS
            for app in app_config:
                if app == app_name:
                    # try to find the app's directory
                    app_module = __import__(app, fromlist=[""])
                    if app_module.__file__ is None:
                        return None
                    app_path = Path(app_module.__file__).parent
                    pages_path = app_path / "pages"
                    if pages_path.exists():
                        return pages_path
        except (ImportError, AttributeError):
            pass
        return None

    def scan_pages_directory(self, pages_path: Path) -> list[tuple[str, Path]]:
        """Scan pages directory for page.py files and return (url_path, file_path) tuples."""
        pages: list[tuple[str, Path]] = []

        def scan_recursive(current_path: Path, url_path: str = "") -> None:
            for item in current_path.iterdir():
                if item.is_dir():
                    # check if directory name contains parameters
                    dir_name = item.name
                    new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
                    scan_recursive(item, new_url_path)
                elif item.name == "page.py":
                    pages.append((url_path, item))

        scan_recursive(pages_path)
        return pages

    def parse_param_name_and_type(self, param_str: str) -> tuple[str, str]:
        """Parse parameter string to extract name and type."""
        if ":" in param_str:
            type_name, param_name = param_str.split(":", 1)
            return param_name.strip(), type_name.strip()
        else:
            return param_str.strip(), "str"

    def parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """
        Parse URL path and extract parameters.

        Returns:
            Tuple of (django_url_pattern, parameters_dict)
        """
        django_pattern = url_path
        parameters: dict[str, str] = {}

        # handle [[args]] pattern first
        args_match = self.args_pattern.search(django_pattern)
        if args_match:
            args_name = args_match.group(1)
            # replace dashes with underscores for valid python identifiers
            django_args_name = args_name.replace("-", "_")
            django_pattern = self.args_pattern.sub(
                f"<path:{django_args_name}>", django_pattern
            )
            parameters[django_args_name] = django_args_name

        # handle [param] pattern after
        param_matches = self.param_pattern.findall(url_path)
        for param_str in param_matches:
            param_name, param_type = self.parse_param_name_and_type(param_str)
            # replace dashes with underscores for valid python identifiers
            django_param_name = param_name.replace("-", "_")
            django_pattern = self.param_pattern.sub(
                f"<{param_type}:{django_param_name}>", django_pattern
            )
            parameters[django_param_name] = django_param_name

        return django_pattern, parameters

    def load_page_function(self, file_path: Path) -> Callable[..., Any] | None:
        """Load render function from page.py file."""
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("page_module", file_path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "render"):
                render_func = module.render
                if callable(render_func):
                    return render_func  # type: ignore[no-any-return]
        except Exception as e:
            print(f"Error loading page function from {file_path}: {e}")

        return None

    def create_url_pattern(self, url_path: str, file_path: Path) -> URLPattern | None:
        """Create Django URL pattern from page.py file."""
        django_pattern, parameters = self.parse_url_pattern(url_path)
        render_func = self.load_page_function(file_path)

        if not render_func:
            return None

        def view_wrapper(request: Any, **kwargs: Any) -> Any:
            """Wrapper to handle parameter passing to render function."""
            # extract args parameter if present
            if "args" in parameters and "args" in kwargs:
                args_list = kwargs["args"].split("/")
                kwargs["args"] = args_list

            return render_func(request, **kwargs)

        return path(
            django_pattern, view_wrapper, name=f"page_{url_path.replace('/', '_')}"
        )

    def generate_urls_for_app(self, app_name: str) -> list[URLPattern | URLResolver]:
        """Generate URL patterns for a specific Django app."""
        if app_name in self.patterns_cache:
            return self.patterns_cache[app_name]

        pages_path = self.get_app_pages_path(app_name)
        if not pages_path:
            return []

        pages = self.scan_pages_directory(pages_path)
        patterns: list[URLPattern | URLResolver] = []

        for url_path, file_path in pages:
            pattern = self.create_url_pattern(url_path, file_path)
            if pattern:
                patterns.append(pattern)

        self.patterns_cache[app_name] = patterns
        return patterns

    def include_app_pages(self, app_name: str) -> URLResolver:
        """Create include() for app pages."""
        patterns = self.generate_urls_for_app(app_name)
        return include((patterns, app_name), namespace=f"{app_name}_pages")  # type: ignore[return-value]


# global router instance
file_router = FileRouter()


def include_pages(app_name: str) -> URLResolver:
    """
    Include pages for a Django app.

    Usage:
        path('', include_pages('myapp'))
    """
    return file_router.include_app_pages(app_name)


def auto_include_all_pages() -> list[URLResolver]:
    """
    Automatically include pages for all Django apps.

    Usage:
        urlpatterns = [
            path('', include(auto_include_all_pages()))
        ]
    """
    resolvers: list[URLResolver] = []
    for app_name in settings.INSTALLED_APPS:
        if not app_name.startswith("django."):
            patterns = file_router.generate_urls_for_app(app_name)
            if patterns:
                resolvers.append(include_pages(app_name))

    return resolvers


# empty urlpatterns - will be generated on demand
urlpatterns: list[URLPattern | URLResolver] = []
