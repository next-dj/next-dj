"""Django signals emitted by realtime brokers, topic streams and upstream handlers."""

import enum

from django.dispatch import Signal

from .pool import PooledSignal


broker_backend_loaded: Signal = Signal(use_caching=True)
message_published: Signal = PooledSignal(use_caching=True)
upstream_received: Signal = PooledSignal(use_caching=True)
upstream_rejected: Signal = PooledSignal(use_caching=True)
stream_subscribed: Signal = PooledSignal(use_caching=True)
stream_unsubscribed: Signal = PooledSignal(use_caching=True)


class UnsubscribeReason(enum.StrEnum):
    """Why a stream left its topics, the `reason` that `stream_unsubscribed` carries.

    SSE reads a cancellation as the client leaving, Django cancels on disconnect.
    """

    CLIENT = "client"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    ERROR = "error"
    SERVER = "server"


__all__ = [
    "UnsubscribeReason",
    "broker_backend_loaded",
    "message_published",
    "stream_subscribed",
    "stream_unsubscribed",
    "upstream_received",
    "upstream_rejected",
]
