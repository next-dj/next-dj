import json

from next.static import (
    PydanticJsContextSerializer as _FrameworkPydantic,
)


ENVELOPE_VERSION = 1


class PydanticJsContextSerializer(_FrameworkPydantic):
    """Global default. Subclass kept for clarity in settings."""


class WrappedJsContextSerializer:
    """Per-key override that wraps the payload in a versioned envelope.

    Encoding flows through `PydanticJsContextSerializer` so pydantic
    models inside the wrapped payload still serialise correctly.
    """

    def __init__(self) -> None:
        """Build the inner serializer eagerly to surface missing pydantic up front."""
        self._inner = PydanticJsContextSerializer()

    def dumps(self, value: object) -> str:
        """Return `{"v": <ENVELOPE_VERSION>, "data": <inner-encoded value>}`."""
        encoded_data = self._inner.dumps(value)
        encoded_version = json.dumps(ENVELOPE_VERSION, separators=(",", ":"))
        return '{"v":' + encoded_version + ',"data":' + encoded_data + "}"
