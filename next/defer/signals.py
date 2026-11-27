"""Django signals of deferred jobs, boundaries, page streams, tokens and backends."""

from django.dispatch import Signal


boundary_job_started: Signal = Signal(use_caching=True)
boundary_job_finished: Signal = Signal(use_caching=True)
boundary_settled: Signal = Signal(use_caching=True)
boundary_state_written: Signal = Signal(use_caching=True)
page_stream_opened: Signal = Signal(use_caching=True)
page_stream_closed: Signal = Signal(use_caching=True)
page_stream_declined: Signal = Signal(use_caching=True)
boundary_token_rejected: Signal = Signal(use_caching=True)
defer_backend_loaded: Signal = Signal(use_caching=True)


__all__ = [
    "boundary_job_finished",
    "boundary_job_started",
    "boundary_settled",
    "boundary_state_written",
    "boundary_token_rejected",
    "defer_backend_loaded",
    "page_stream_closed",
    "page_stream_declined",
    "page_stream_opened",
]
