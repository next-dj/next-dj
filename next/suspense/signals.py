"""Django signals of settled deferred boundaries, page streams and refused tokens."""

from django.dispatch import Signal


boundary_rendered: Signal = Signal(use_caching=True)
boundary_side_effect: Signal = Signal(use_caching=True)
page_stream_opened: Signal = Signal(use_caching=True)
page_stream_closed: Signal = Signal(use_caching=True)
page_stream_declined: Signal = Signal(use_caching=True)
boundary_token_rejected: Signal = Signal(use_caching=True)


__all__ = [
    "boundary_rendered",
    "boundary_side_effect",
    "boundary_token_rejected",
    "page_stream_closed",
    "page_stream_declined",
    "page_stream_opened",
]
