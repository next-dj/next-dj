"""Publish subscriber-safe patch envelopes to realtime topics and read who is there."""

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Literal, overload

from django.db import transaction

from next.partial.envelope import Envelope
from next.partial.headers import partial_intent
from next.partial.patches import Patches

from .errors import TopicPayloadError
from .manager import broker_manager
from .messages import Message, validate_topic
from .roster import Presence, presence_broker
from .signals import apublished, published


_ALLOWED_OPS = frozenset({"refresh", "event", "toast", "remove"})


def _prepare(patches: object) -> Envelope:
    """Build an envelope, stamp request echo metadata, and validate its wire form."""
    if isinstance(patches, Patches):
        envelope = patches.envelope()
        if envelope.request_id is None and patches.request is not None:
            request_id = partial_intent(patches.request).request_id
            if request_id is not None:
                envelope = replace(envelope, request_id=request_id)
    elif isinstance(patches, Envelope):
        envelope = patches
    else:
        msg = "publish() requires a Patches builder or partial Envelope."
        raise TypeError(msg)

    return _validate_topic_payload(envelope)


def _validate_topic_payload(envelope: Envelope) -> Envelope:
    """Validate and detach JSON data before deferred publication can retain it."""
    if envelope.assets:
        reason = "assets are not allowed"
        raise TopicPayloadError(reason)
    if envelope.form is not None:
        reason = "form metadata is not allowed"
        raise TopicPayloadError(reason)
    if envelope.csrf is not None:
        reason = "CSRF metadata is not allowed"
        raise TopicPayloadError(reason)
    for patch in envelope.ops:
        if patch.op not in _ALLOWED_OPS:
            reason = f"patch operation {patch.op!r} is not allowed"
            raise TopicPayloadError(reason)
        if patch.html is not None:
            reason = f"patch operation {patch.op!r} carries HTML"
            raise TopicPayloadError(reason)
    try:
        snapshot = json.loads(json.dumps(envelope.as_dict(), allow_nan=False))
    except (TypeError, ValueError) as exc:
        reason = "the envelope is not JSON serializable"
        raise TopicPayloadError(reason) from exc
    return Envelope.from_dict(snapshot)


def transformed(message: Message, result: object) -> Message | None:
    """Return the message carrying a subscriber transform's result, None for a skip."""
    if result is None:
        return None
    return replace(message, envelope=_prepare(result))


def _publish_now(topic: str, envelope: Envelope) -> Message:
    """Publish once, send `message_published` after the broker accepts it, return it."""
    backend = broker_manager.get()
    return published(backend, backend.publish(topic, envelope))


def _deferred(topic: str, envelope: Envelope) -> Callable[[], None]:
    """Build a commit callback whose qualname names the topic in Django's error log.

    Django 5.2 reads the qualname of a robust callback without a fallback.
    """

    def deliver() -> None:
        _publish_now(topic, envelope)

    deliver.__qualname__ = f"publish({topic!r})"
    return deliver


@overload
def publish(
    topic: str,
    patches: Patches | Envelope,
    *,
    on_commit: Literal[False],
    using: str | None = None,
) -> Message: ...


@overload
def publish(
    topic: str,
    patches: Patches | Envelope,
    *,
    on_commit: bool = True,
    using: str | None = None,
) -> Message | None: ...


def publish(
    topic: str,
    patches: Patches | Envelope,
    *,
    on_commit: bool = True,
    using: str | None = None,
) -> Message | None:
    """Publish safe patch instructions and return the message, None once deferred.

    A deferred broker failure is logged by Django instead of failing a committed write.
    """
    validated_topic = validate_topic(topic)
    envelope = _prepare(patches)
    if not on_commit:
        return _publish_now(validated_topic, envelope)
    transaction.on_commit(
        _deferred(validated_topic, envelope), using=using, robust=True
    )
    return None


async def apublish(topic: str, patches: Patches | Envelope) -> Message:
    """Publish safe patch instructions through the broker's async API."""
    validated_topic = validate_topic(topic)
    envelope = _prepare(patches)
    backend = await broker_manager.aget()
    return await apublished(backend, await backend.apublish(validated_topic, envelope))


def presence(topic: str) -> Presence:
    """Return the attendance snapshot of a topic, refusing a broker without presence."""
    broker = presence_broker(broker_manager.get())
    return broker.presence(validate_topic(topic))


async def apresence(topic: str) -> Presence:
    """Return the attendance snapshot of a topic through the broker's async API."""
    broker = presence_broker(await broker_manager.aget())
    return await broker.apresence(validate_topic(topic))


__all__ = ["apresence", "apublish", "presence", "publish", "transformed"]
