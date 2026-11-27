"""Job safety for rendering on a pool thread, facts, replay, settled request state.

The view's thread reads the facts that pin a job to the request lane and captures the
state a pool thread replays before each job, so a job sees the connections it would see.
"""

from .backends import ConnectionState, Seams, ThreadState, seams
from .detection import (
    TENANT_MIDDLEWARE,
    THREAD_LOCAL_LIBRARIES,
    connection_sources,
    thread_sources,
)
from .facts import Outlived, States, capture_states, forcing_pool, lane_facts, server_of
from .guard import (
    DEADLINE_SQL,
    Replayable,
    deadline_guard,
    deadline_statement,
    job_guard,
    timeout_ms,
)
from .settle import Settled, effects, request_state, settle_request
from .snapshot import fresh_querysets


__all__ = [
    "DEADLINE_SQL",
    "TENANT_MIDDLEWARE",
    "THREAD_LOCAL_LIBRARIES",
    "ConnectionState",
    "Outlived",
    "Replayable",
    "Seams",
    "Settled",
    "States",
    "ThreadState",
    "capture_states",
    "connection_sources",
    "deadline_guard",
    "deadline_statement",
    "effects",
    "forcing_pool",
    "fresh_querysets",
    "job_guard",
    "lane_facts",
    "request_state",
    "seams",
    "server_of",
    "settle_request",
    "thread_sources",
    "timeout_ms",
]
