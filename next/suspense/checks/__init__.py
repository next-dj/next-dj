"""System checks for the `DEFER` scope, token seams, connections, streams and calls.

Importing the package registers every check.
"""

from .backends import check_defer_backends
from .calls import check_client_calls, check_declared_props, check_prop_types
from .connections import (
    check_connection_sources,
    check_pool_budget,
    check_pool_timeout,
    check_thread_sources,
)
from .jobs import check_durable_jobs
from .settings import check_defer_settings
from .streams import (
    check_buffering_gzip,
    check_bundles_have_a_runtime,
    check_stream_nonce,
)
from .tokens import (
    check_defer_tokens,
    check_token_binding,
    check_token_cache,
    check_token_keys,
)


__all__ = [
    "check_buffering_gzip",
    "check_bundles_have_a_runtime",
    "check_client_calls",
    "check_connection_sources",
    "check_declared_props",
    "check_defer_backends",
    "check_defer_settings",
    "check_defer_tokens",
    "check_durable_jobs",
    "check_pool_budget",
    "check_pool_timeout",
    "check_prop_types",
    "check_stream_nonce",
    "check_thread_sources",
    "check_token_binding",
    "check_token_cache",
    "check_token_keys",
]
