"""Errors raised while configuring realtime brokers and topics."""


class InvalidTopicError(ValueError):
    """Raised when a topic name is malformed or uses a reserved prefix."""

    def __init__(self, topic: str) -> None:
        """Store the rejected topic and describe the accepted format."""
        self.topic = topic
        super().__init__(
            f"Invalid realtime topic {topic!r}. Use 1-200 ASCII letters, digits, "
            "underscores, dots, colons, or hyphens, and do not start with 'next:'."
        )


class TopicPayloadError(ValueError):
    """Raised when an envelope could expose subscriber-specific content."""

    def __init__(self, reason: str) -> None:
        """Describe the rejected part and recommend a subscriber-local refresh."""
        self.reason = reason
        super().__init__(
            "Realtime topics accept only refresh, event, toast, and remove patches. "
            f"Rejected because {reason}. Send refresh(zone=...) so each subscriber "
            "loads its own HTML."
        )


class InvalidTicketError(ValueError):
    """Raised when a WebSocket ticket cannot safely grant a topic subscription."""


class InvalidMemberError(ValueError):
    """Raised when a presence member is not a small mapping of JSON values."""

    def __init__(self, reason: str) -> None:
        """Store why the member was refused and name the setting that bounds it."""
        self.reason = reason
        super().__init__(
            "A presence member must be a JSON object of at most "
            f"REALTIME.PRESENCE.MEMBER_BYTES bytes. Rejected because {reason}."
        )


class BrokerConnectionError(ConnectionError):
    """Raised when a broker subscription can no longer guarantee delivery."""


__all__ = [
    "BrokerConnectionError",
    "InvalidMemberError",
    "InvalidTicketError",
    "InvalidTopicError",
    "TopicPayloadError",
]
