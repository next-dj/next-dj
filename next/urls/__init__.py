"""URL routing, router backends, and URL parameter injection providers.

`__all__` is the supported surface. Advanced callers reach the walk
helpers and `_LazyUrlPatterns` through deep imports, which keeps the
package namespace small.
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
