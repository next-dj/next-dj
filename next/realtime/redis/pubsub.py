"""Redis Pub/Sub subscriptions, confirmed before use and probed while they idle."""

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol

from next.realtime.errors import BrokerConnectionError
from next.realtime.messages import Message


logger = logging.getLogger(__name__)

_SUBSCRIBE_TIMEOUT = 5.0
_LIVENESS = "next-realtime-liveness"
_UNCONFIRMED = "Redis did not confirm the subscription in time."

type Event = Mapping[str, Any]


class PubSub(Protocol):
    """The sync Pub/Sub connection of one subscription."""

    def subscribe(self, *channels: str) -> object:
        """Subscribe to every channel, acknowledged by later events."""

    def get_message(self, *, timeout: float) -> Event | None:
        """Return the next event, None once `timeout` passes without one."""

    def ping(self, message: str) -> object:
        """Ask Redis to echo `message` on the subscription connection."""

    def close(self) -> None:
        """Release the subscription connection."""


class AsyncPubSub(Protocol):
    """The asynchronous Pub/Sub connection of one subscription."""

    def subscribe(self, *channels: str) -> Awaitable[object]:
        """Subscribe to every channel, acknowledged by later events."""

    def get_message(self, *, timeout: float) -> Awaitable[Event | None]:
        """Return the next event, None once `timeout` passes without one."""

    def ping(self, message: str) -> Awaitable[object]:
        """Ask Redis to echo `message` on the subscription connection."""

    def aclose(self) -> Awaitable[None]:
        """Release the subscription connection."""


def decode_message(payload: object) -> Message | None:
    """Decode one published payload, logging and skipping a malformed one."""
    try:
        decoded = json.loads(payload) if isinstance(payload, str | bytes) else None
        message = Message.from_dict(decoded) if isinstance(decoded, dict) else None
    except (TypeError, ValueError):
        message = None
    if message is None:
        logger.warning("Skipped a malformed realtime message from Redis.")
    return message


def event_message(event: Event) -> Message | None:
    """Decode a published frame and treat a fresh acknowledgement as reconnect."""
    kind = event.get("type")
    if kind == "subscribe":
        msg = "Redis reconnected a subscription, so messages may have been lost."
        raise BrokerConnectionError(msg)
    if kind != "message":
        return None
    return decode_message(event.get("data"))


class Confirmation:
    """Tick off the acknowledgements of new channels before a shared deadline.

    A message published during setup is kept, so the stream yields it first.
    """

    def __init__(self, channels: Iterable[str]) -> None:
        """Wait for every channel, the deadline starting now."""
        self.waiting = set(channels)
        self.buffered: list[Message] = []
        self._deadline = time.monotonic() + _SUBSCRIBE_TIMEOUT

    def remaining(self) -> float:
        """Return the seconds left to wait, failing once the deadline passed."""
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise BrokerConnectionError(_UNCONFIRMED)
        return left

    def feed(self, event: Event | None) -> None:
        """Tick off one acknowledgement or keep a message that arrived meanwhile."""
        if event is None:
            return
        if event.get("type") == "subscribe":
            self.waiting.discard(event.get("channel", ""))
            return
        message = event_message(event)
        if message is not None:
            self.buffered.append(message)


def confirm(pubsub: PubSub, channels: Iterable[str]) -> list[Message]:
    """Consume every subscribe acknowledgement, keeping the messages seen meanwhile."""
    confirmation = Confirmation(channels)
    while confirmation.waiting:
        confirmation.feed(pubsub.get_message(timeout=confirmation.remaining()))
    return confirmation.buffered


async def aconfirm(pubsub: AsyncPubSub, channels: Iterable[str]) -> list[Message]:
    """Consume every subscribe acknowledgement without blocking the loop."""
    confirmation = Confirmation(channels)
    while confirmation.waiting:
        confirmation.feed(await pubsub.get_message(timeout=confirmation.remaining()))
    return confirmation.buffered


class Watch:
    """Track the heartbeat deadline and the liveness probe of one subscription."""

    def __init__(self, timeout: float) -> None:
        """Start the heartbeat deadline `timeout` seconds from now."""
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.probing = False

    def remaining(self) -> float:
        """Return the seconds left before the next heartbeat is due."""
        return self.deadline - time.monotonic()

    def expire(self) -> None:
        """Start a liveness probe, failing when the previous one went unanswered."""
        if self.probing:
            msg = "Redis stopped answering on a subscription connection."
            raise BrokerConnectionError(msg)
        self.probing = True
        self.deadline = time.monotonic() + self.timeout

    def answered(self, *, delivered: bool) -> None:
        """Record a frame from Redis and restart the heartbeat after a message."""
        self.probing = False
        if delivered:
            self.deadline = time.monotonic() + self.timeout


def messages(
    pubsub: PubSub,
    buffered: list[Message],
    wait_seconds: float,
    failures: AbstractContextManager[None],
) -> Iterator[Message | None]:
    """Yield the buffered messages, then live ones and a None for each heartbeat."""
    yield from buffered
    watch = Watch(wait_seconds)
    while True:
        with failures:
            event = pubsub.get_message(timeout=max(watch.remaining(), 0.0))
        if event is not None:
            message = event_message(event)
            watch.answered(delivered=message is not None)
            if message is not None:
                yield message
        elif watch.remaining() <= 0:
            watch.expire()
            with failures:
                pubsub.ping(_LIVENESS)
            yield None


async def amessages(
    pubsub: AsyncPubSub,
    buffered: list[Message],
    wait_seconds: float,
    failures: AbstractContextManager[None],
) -> AsyncIterator[Message | None]:
    """Yield the buffered messages, then live ones, without blocking the loop."""
    for early in buffered:
        yield early
    watch = Watch(wait_seconds)
    while True:
        with failures:
            event = await pubsub.get_message(timeout=max(watch.remaining(), 0.0))
        if event is not None:
            message = event_message(event)
            watch.answered(delivered=message is not None)
            if message is not None:
                yield message
        elif watch.remaining() <= 0:
            watch.expire()
            with failures:
                await pubsub.ping(_LIVENESS)
            yield None


__all__ = [
    "AsyncPubSub",
    "Confirmation",
    "Event",
    "PubSub",
    "Watch",
    "aconfirm",
    "amessages",
    "confirm",
    "decode_message",
    "event_message",
    "messages",
]
