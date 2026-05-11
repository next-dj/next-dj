"""JS-context serializers used by the dashboard.

Two classes ship here. `PydanticJsContextSerializer` is the example's
re-export of the framework class and is wired globally through
`JS_CONTEXT_SERIALIZER` in `config/settings.py`. It produces the same
flat JSON for every key. `WrappedJsContextSerializer` is the per-key
override pinned on `live_stats` and on the sparkline's
`render_rates` key. It wraps the payload in a versioned envelope so a
glance at `window.Next.context.live_stats` proves the override is the
one shaping the value: every other key is flat, and only the two
overridden keys carry `{"v": 1, "data": ...}`.
"""

import json
from typing import Any

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

    def dumps(self, value: Any) -> str:  # noqa: ANN401
        """Return `{"v": <ENVELOPE_VERSION>, "data": <inner-encoded value>}`."""
        encoded_data = self._inner.dumps(value)
        encoded_version = json.dumps(ENVELOPE_VERSION, separators=(",", ":"))
        return '{"v":' + encoded_version + ',"data":' + encoded_data + "}"
