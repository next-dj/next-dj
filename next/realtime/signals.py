"""Django signals emitted by realtime brokers, topic streams and upstream handlers."""

import enum
import logging
import time
from collections.abc import Sequence

from django.dispatch import Signal

from .messages import Message


broker_backend_loaded: Signal = Signal(use_caching=True)
message_published: Signal = Signal(use_caching=True)
upstream_received: Signal = Signal(use_caching=True)
stream_subscribed: Signal = Signal(use_caching=True)
stream_unsubscribed: Signal = Signal(use_caching=True)

logger = logging.getLogger(__name__)


class UnsubscribeReason(enum.StrEnum):
    """Why a stream left its topics, the `reason` that `stream_unsubscribed` carries.

    SSE reads a cancellation as the client leaving, Django cancels on disconnect.
    """

    CLIENT = "client"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    ERROR = "error"
    SERVER = "server"


class StreamLifecycle:
    """Send one stream's subscribe signal and the matching unsubscribe signal once.

    Nothing is sent or timed when neither signal has a receiver as the stream opens.
    """

    __slots__ = ("_connection", "_opened_at", "_topics", "_transport", "reason")

    def __init__(self, connection: object, transport: str) -> None:
        """Bind the signals to the stream object they report on and its transport."""
        self._connection = connection
        self._transport = transport
        self._topics: tuple[str, ...] = ()
        self._opened_at: float | None = None
        self.reason: UnsubscribeReason | None = None

    def open(
        self,
        topics: Sequence[str],
        *,
        request: object = None,
        subject: str | None = None,
        user: object = None,
    ) -> None:
        """Send `stream_subscribed` for a subscription the broker just opened."""
        sender = type(self._connection)
        if self._opened(sender, topics):
            self._log(
                "stream_subscribed",
                stream_subscribed.send_robust(
                    sender=sender,
                    **self._identity(),
                    request=request,
                    subject=subject,
                    user=user,
                ),
            )

    async def aopen(
        self,
        topics: Sequence[str],
        *,
        request: object = None,
        subject: str | None = None,
        user: object = None,
    ) -> None:
        """Send `stream_subscribed` from an event loop, sync receivers off it."""
        sender = type(self._connection)
        if self._opened(sender, topics):
            self._log(
                "stream_subscribed",
                await stream_subscribed.asend_robust(
                    sender=sender,
                    **self._identity(),
                    request=request,
                    subject=subject,
                    user=user,
                ),
            )

    def close(self, reason: UnsubscribeReason) -> None:
        """Send `stream_unsubscribed` once, a reason noted earlier winning."""
        kwargs = self._closed(reason)
        if kwargs is not None:
            self._log(
                "stream_unsubscribed",
                stream_unsubscribed.send_robust(
                    sender=type(self._connection), **kwargs
                ),
            )

    async def aclose(self, reason: UnsubscribeReason) -> None:
        """Send `stream_unsubscribed` once from an event loop."""
        kwargs = self._closed(reason)
        if kwargs is not None:
            self._log(
                "stream_unsubscribed",
                await stream_unsubscribed.asend_robust(
                    sender=type(self._connection), **kwargs
                ),
            )

    def _opened(self, sender: type, topics: Sequence[str]) -> bool:
        """Time the stream if any signal listens, True if `stream_subscribed` does."""
        if not (
            stream_subscribed.has_listeners(sender)
            or stream_unsubscribed.has_listeners(sender)
        ):
            return False
        self._topics = tuple(topics)
        self._opened_at = time.monotonic()
        return stream_subscribed.has_listeners(sender)

    def _identity(self) -> dict[str, object]:
        return {
            "transport": self._transport,
            "topics": self._topics,
            "connection": self._connection,
        }

    def _closed(self, reason: UnsubscribeReason) -> dict[str, object] | None:
        opened_at, self._opened_at = self._opened_at, None
        if opened_at is None:
            return None
        return {
            **self._identity(),
            "duration": time.monotonic() - opened_at,
            "reason": self.reason or reason,
        }

    def _log(self, signal: str, responses: Sequence[tuple[object, object]]) -> None:
        """Log each receiver that raised, the stream going on without it."""
        for receiver, response in responses:
            if isinstance(response, Exception):
                logger.error(
                    "Realtime %s receiver %r failed on %s topics %s",
                    signal,
                    receiver,
                    self._transport,
                    ", ".join(self._topics),
                    exc_info=response,
                )


def published(broker: object, message: Message) -> Message:
    """Send `message_published` for a message the broker accepted and return it."""
    message_published.send(sender=type(broker), topic=message.topic, message=message)
    return message


__all__ = [
    "UnsubscribeReason",
    "broker_backend_loaded",
    "message_published",
    "stream_subscribed",
    "stream_unsubscribed",
    "upstream_received",
]
