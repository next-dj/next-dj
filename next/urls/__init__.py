"""URL routing, router backends, and URL parameter injection providers.

Built-in entry points.

- `RouterBackend` and `FileRouterBackend` as extension surface.
- `RouterFactory` to map dotted paths to backend classes.
- `RouterManager` plus singleton `router_manager`.
- `URLPatternParser` for bracket-segment parsing.
- `DUrl` marker used in `@context` annotations for URL path segments.
- `DQuery` marker used in `@context` annotations for query parameters.
- Django integration via `app_name` and `urlpatterns`.

Deep-import paths expose `FilesystemTreeDispatcher`, `scan_pages_tree`,
the parameter providers, and `_LazyUrlPatterns` for advanced callers.
"""

from . import checks, signals
from .backends import FileRouterBackend, RouterBackend, RouterFactory
from .manager import RouterManager, app_name, router_manager, urlpatterns
from .markers import (
    DQuery,
    DUrl,
    HttpRequestProvider,
    QueryParamProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
)
from .parser import URLPatternParser


__all__ = [
    "DQuery",
    "DUrl",
    "FileRouterBackend",
    "HttpRequestProvider",
    "QueryParamProvider",
    "RouterBackend",
    "RouterFactory",
    "RouterManager",
    "URLPatternParser",
    "UrlByAnnotationProvider",
    "UrlKwargsProvider",
    "app_name",
    "checks",
    "router_manager",
    "signals",
    "urlpatterns",
]
