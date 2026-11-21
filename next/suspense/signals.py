"""Django signals of settled deferred boundaries, page streams and refused tokens."""

from django.dispatch import Signal


boundary_rendered: Signal = Signal(use_caching=True)
"""Sent once per settled deferred job, on the request thread that assembled it."""

boundary_side_effect: Signal = Signal(use_caching=True)
"""Sent once per request-state write a deferred job made, which jobs do not support."""

page_stream_opened: Signal = Signal(use_caching=True)
"""Sent once per streamed page or partial response, before its first byte is written."""

page_stream_closed: Signal = Signal(use_caching=True)
"""Sent once per streamed page or partial response when its body ends, or aborted."""

page_stream_declined: Signal = Signal(use_caching=True)
"""Sent once per page or partial response whose deferred jobs are assembled inline."""

boundary_token_rejected: Signal = Signal(use_caching=True)
"""Sent once per refused boundary token, with the reason no client is ever told."""


__all__ = [
    "boundary_rendered",
    "boundary_side_effect",
    "boundary_token_rejected",
    "page_stream_closed",
    "page_stream_declined",
    "page_stream_opened",
]
