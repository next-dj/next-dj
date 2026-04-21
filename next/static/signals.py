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
is sealed. The sender is the collector. The keyword argument is
`page_path`, which may be None for partial renders.

The `html_injected` signal fires after placeholder replacement
completes. The sender is the static manager. The keyword arguments
are `html_before`, `html_after`, and `collector`.

The `backend_loaded` signal fires after the static factory
instantiates a backend. The sender is the backend class. The keyword
arguments are `config` and `instance`.
"""

from __future__ import annotations

from django.dispatch import Signal


asset_registered: Signal = Signal()
collector_finalized: Signal = Signal()
html_injected: Signal = Signal()
backend_loaded: Signal = Signal()
