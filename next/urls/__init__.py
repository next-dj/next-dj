"""URL routing, router backends, and URL parameter injection providers.

`_LazyUrlPatterns` and the tree-walk helpers stay out of `__all__` on
purpose. They are wiring internals, and a caller that needs one imports
it from the module that defines it.
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
    get_multi_values,
)
from .parser import DuplicateURLParameterError, URLPatternParser
from .resolver import TrieURLResolver
from .reverse import page_reverse, page_reverse_lazy, with_query


__all__ = [
    "DQuery",
    "DUrl",
    "DuplicateURLParameterError",
    "FileRouterBackend",
    "HttpRequestProvider",
    "QueryParamProvider",
    "RouterBackend",
    "RouterFactory",
    "RouterManager",
    "TrieURLResolver",
    "URLPatternParser",
    "UrlByAnnotationProvider",
    "UrlKwargsProvider",
    "app_name",
    "checks",
    "get_multi_values",
    "page_reverse",
    "page_reverse_lazy",
    "router_manager",
    "signals",
    "urlpatterns",
    "with_query",
]
