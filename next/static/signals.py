"""Django signals emitted across the static pipeline.

Signals are the primary extension mechanism for hooking into asset
lifecycle events without subclassing the collector, the backend, or
the manager. Subscribe from `AppConfig.ready` and keep handlers
synchronous. All four signals are dispatched in hot rendering paths.

The `asset_registered` signal fires after a file is registered with a
backend and added to the collector. The sender is the asset instance
and the keyword arguments are `collector` and `backend`.

The `collector_finalized` signal fires when the static manager begins
injection, after template rendering has completed and the collector
is sealed. The sender is the collector. The keyword arguments are
`page_path`, the rendered page's file path, and `request`, the active
`HttpRequest` or None for renders outside a request lifecycle. A
standalone zone render does not fire this signal, since it ships its
assets through the patch envelope rather than through injection.

The `html_injected` signal fires after placeholder replacement
completes. The sender is the static manager. The keyword arguments
are `html_before`, `html_after`, `collector`, `placeholders_replaced`,
`injected_bytes`, and `request`. The `request` argument carries the
active `HttpRequest` or None.

The `backend_loaded` signal fires after the shared backend loader
instantiates a static backend. The sender is the backend class. The
keyword arguments are `config` and `instance`.
"""

from django.dispatch import Signal


asset_registered: Signal = Signal()
collector_finalized: Signal = Signal()
html_injected: Signal = Signal()
backend_loaded: Signal = Signal()
