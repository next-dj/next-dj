"""Publish validated, subscriber-safe patch envelopes to realtime topics."""

import json
from dataclasses import dataclass, field, replace

from django.db import transaction

from next.partial.envelope import Envelope
from next.partial.headers import partial_intent
from next.partial.patches import Patches

from .errors import TopicPayloadError
from .manager import broker_manager
from .messages import Message, validate_topic
from .signals import message_published


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


def _publish_now(topic: str, envelope: Envelope) -> None:
    """Publish once and emit the lifecycle signal after broker acceptance."""
    backend = broker_manager.get()
    message = backend.publish(topic, envelope)
    message_published.send(sender=type(backend), topic=topic, message=message)


@dataclass(frozen=True, slots=True)
class _DeferredPublication:
    """Publish after commit, its repr naming the topic in Django's robust error log."""

    topic: str
    envelope: Envelope = field(repr=False)

    def __call__(self) -> None:
        """Hand the snapshot to the broker once the transaction has committed."""
        _publish_now(self.topic, self.envelope)


def publish(
    topic: str,
    patches: Patches | Envelope,
    *,
    on_commit: bool = True,
    using: str | None = None,
) -> None:
    """Publish safe patch instructions, optionally after the current transaction.

    A deferred broker failure is logged by Django instead of failing a committed write.
    """
    validated_topic = validate_topic(topic)
    envelope = _prepare(patches)
    if on_commit:
        transaction.on_commit(
            _DeferredPublication(validated_topic, envelope), using=using, robust=True
        )
    else:
        _publish_now(validated_topic, envelope)


async def apublish(topic: str, patches: Patches | Envelope) -> Message:
    """Publish safe patch instructions through the broker's async API."""
    validated_topic = validate_topic(topic)
    envelope = _prepare(patches)
    backend = await broker_manager.aget()
    message = await backend.apublish(validated_topic, envelope)
    await message_published.asend(
        sender=type(backend), topic=validated_topic, message=message
    )
    return message


__all__ = ["apublish", "publish"]
