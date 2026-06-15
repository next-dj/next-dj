"""Protocol backend owning the patch wire format, its factory and manager."""

import json
from typing import TYPE_CHECKING, Any, cast

from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded

from .headers import CONTENT_TYPE


if TYPE_CHECKING:
    from collections.abc import Mapping

    from .patches import Envelope


_SSE_EVENT_NAME = "next-patches"
_PARTIAL_BACKENDS_KEY = "PARTIAL_BACKENDS"


class PartialProtocolBackend:
    """Owner of the wire format for patch envelopes.

    The default backend serialises envelopes as a compact JSON envelope
    under `application/vnd.next.patches+json`. A third party may swap the
    wire format, for example to emulate Turbo Streams, by registering a
    different backend through `PARTIAL_BACKENDS` without touching shaping
    or the registries. Both `serialize_envelope` and `sse_event` operate
    over the same JSON envelope.
    """

    content_type: str = CONTENT_TYPE

    def __init__(self, config: "Mapping[str, Any] | None" = None) -> None:
        """Store the merged backend config and its options."""
        self._config: Mapping[str, Any] = config or {}
        options = self._config.get("OPTIONS")
        self._options: Mapping[str, Any] = options if isinstance(options, dict) else {}

    @property
    def options(self) -> "Mapping[str, Any]":
        """Return the backend OPTIONS mapping from settings."""
        return self._options

    def _dumps(self, envelope: "Envelope") -> str:
        """Serialise an envelope to a compact JSON string."""
        return json.dumps(
            envelope.as_dict(),
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def serialize_envelope(self, envelope: "Envelope") -> bytes:
        """Serialize one envelope for an HTTP response body."""
        return self._dumps(envelope).encode("utf-8")

    def sse_event(self, envelope: "Envelope") -> str:
        """Serialize one envelope as an SSE event frame."""
        return f"event: {_SSE_EVENT_NAME}\ndata: {self._dumps(envelope)}\n\n"


class PartialProtocolFactory:
    """Instantiates protocol backends from `PARTIAL_BACKENDS` entries."""

    @classmethod
    def create_backend(cls, config: "Mapping[str, Any]") -> PartialProtocolBackend:
        """Return a single backend instance for one settings entry."""
        backend_path = config["BACKEND"]
        backend_class = import_class_cached(backend_path)
        return cast("PartialProtocolBackend", backend_class(config))


class PartialBackendManager:
    """Lazily instantiates the first configured protocol backend."""

    def __init__(self) -> None:
        """Initialise an empty backend cache."""
        self._backend: PartialProtocolBackend | None = None

    def reset(self) -> None:
        """Drop the cached backend so a reloaded config takes effect."""
        self._backend = None

    def _ensure_backend(self) -> PartialProtocolBackend:
        if self._backend is None:
            configs = getattr(next_framework_settings, _PARTIAL_BACKENDS_KEY, [])
            config = next(
                (c for c in configs if isinstance(c, dict)),
                {"BACKEND": f"{__name__}.PartialProtocolBackend"},
            )
            self._backend = PartialProtocolFactory.create_backend(config)
        return self._backend

    def get(self) -> PartialProtocolBackend:
        """Return the active protocol backend."""
        return self._ensure_backend()


partial_backend_manager = PartialBackendManager()


def _on_settings_reloaded(**_kwargs: object) -> None:
    """Drop the cached backend so a reloaded config takes effect."""
    partial_backend_manager.reset()


settings_reloaded.connect(_on_settings_reloaded)


__all__ = [
    "PartialBackendManager",
    "PartialProtocolBackend",
    "PartialProtocolFactory",
    "partial_backend_manager",
]
