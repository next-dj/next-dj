"""Django signals emitted by realtime broker management."""

from django.dispatch import Signal

from .messages import Message


broker_backend_loaded: Signal = Signal(use_caching=True)
message_published: Signal = Signal(use_caching=True)


def published(broker: object, message: Message) -> Message:
    """Send `message_published` for a message the broker accepted and return it."""
    message_published.send(sender=type(broker), topic=message.topic, message=message)
    return message


__all__ = ["broker_backend_loaded", "message_published"]
