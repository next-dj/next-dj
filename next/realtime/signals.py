"""Django signals emitted by realtime brokers, topic streams and upstream handlers."""

import enum
import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.dispatch import Signal

from .messages import Message


if TYPE_CHECKING:
    from .handlers import Upstream


broker_backend_loaded: Signal = Signal(use_caching=True)
message_published: Signal = Signal(use_caching=True)
upstream_received: Signal = Signal(use_caching=True)
upstream_rejected: Signal = Signal(use_caching=True)
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
        topics = ", ".join(self._topics)
        log_failures(signal, responses, f"{self._transport} topics", topics)


def log_failures(
    signal: str, responses: Sequence[tuple[object, object]], scope: str, name: str
) -> None:
    """Log each receiver a robust send caught raising, the caller going on."""
    for receiver, response in responses:
        if isinstance(response, Exception):
            logger.error(
                "Realtime %s receiver %r failed on %s %s",
                signal,
                receiver,
                scope,
                name,
                exc_info=response,
            )


def published(broker: object, message: Message) -> Message:
    """Send `message_published` for a message the broker accepted and return it."""
    responses = message_published.send_robust(
        sender=type(broker),
        topic=message.topic,
        message=message,
        ephemeral=not message.numbered,
    )
    log_failures("message_published", responses, "topic", message.topic)
    return message


async def apublished(broker: object, message: Message) -> Message:
    """Send `message_published` from an event loop and return the message."""
    responses = await message_published.asend_robust(
        sender=type(broker),
        topic=message.topic,
        message=message,
        ephemeral=not message.numbered,
    )
    log_failures("message_published", responses, "topic", message.topic)
    return message


async def areceived(handler: object, upstream: "Upstream") -> None:
    """Send `upstream_received` for an event about to reach its handler."""
    responses = await upstream_received.asend_robust(sender=handler, upstream=upstream)
    log_failures("upstream_received", responses, "topic", upstream.topic)


async def arejected(
    sender: type, *, topic: str, name: str, reason: str, connection: str, subject: str
) -> None:
    """Send `upstream_rejected` for an event the socket refused to deliver."""
    responses = await upstream_rejected.asend_robust(
        sender=sender,
        topic=topic,
        name=name,
        reason=reason,
        connection=connection,
        subject=subject,
    )
    log_failures("upstream_rejected", responses, "topic", topic)


__all__ = [
    "UnsubscribeReason",
    "broker_backend_loaded",
    "message_published",
    "stream_subscribed",
    "stream_unsubscribed",
    "upstream_received",
    "upstream_rejected",
]
