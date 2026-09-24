"""Django signals emitted by the partial-rendering subsystem."""

from django.dispatch import Signal


zone_registered: Signal = Signal(use_caching=True)
zone_rendered: Signal = Signal(use_caching=True)
patch_op_registered: Signal = Signal(use_caching=True)
field_validated: Signal = Signal(use_caching=True)
sse_stream_opened: Signal = Signal(use_caching=True)
sse_stream_closed: Signal = Signal(use_caching=True)
partial_backend_loaded: Signal = Signal(use_caching=True)


__all__ = [
    "field_validated",
    "partial_backend_loaded",
    "patch_op_registered",
    "sse_stream_closed",
    "sse_stream_opened",
    "zone_registered",
    "zone_rendered",
]
