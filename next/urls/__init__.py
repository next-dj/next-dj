"""URL routing, page backends, and URL parameter injection providers.

`_LazyUrlPatterns` and the tree-walk helpers are internals, kept out of `__all__`.
"""

from next.utils import PageRoot

from . import checks, signals
from .backends import FilePageBackend, PageBackend
from .errors import (
    DuplicateURLParameterError,
    InvalidURLParameterError,
    ReservedURLParameterError,
    URLParameterError,
)
from .manager import RouteManager, app_name, route_manager, urlpatterns
from .markers import (
    DQuery,
    DUrl,
    HttpRequestProvider,
    QueryParamProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
    get_multi_values,
)
from .mount import pages
from .parser import URLPatternParser
from .resolver import TrieURLResolver
from .reverse import page_reverse, page_reverse_in, page_reverse_lazy, with_query


__all__ = [
    "DQuery",
    "DUrl",
    "DuplicateURLParameterError",
    "FilePageBackend",
    "HttpRequestProvider",
    "InvalidURLParameterError",
    "PageBackend",
    "PageRoot",
    "QueryParamProvider",
    "ReservedURLParameterError",
    "RouteManager",
    "TrieURLResolver",
    "URLParameterError",
    "URLPatternParser",
    "UrlByAnnotationProvider",
    "UrlKwargsProvider",
    "app_name",
    "checks",
    "get_multi_values",
    "page_reverse",
    "page_reverse_in",
    "page_reverse_lazy",
    "pages",
    "route_manager",
    "signals",
    "urlpatterns",
    "with_query",
]
