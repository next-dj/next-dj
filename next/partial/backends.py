"""Protocol backend owning the patch wire format, its factory and manager."""

import hashlib
import json
from typing import TYPE_CHECKING, Any, cast

from django.contrib.staticfiles.storage import (
    ManifestFilesMixin,
    staticfiles_storage,
)
from django.core.exceptions import ImproperlyConfigured

from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded

from .headers import CONTENT_TYPE


if TYPE_CHECKING:
    from collections.abc import Mapping

    from .patches import Envelope


_SSE_EVENT_NAME = "next-patches"
_PARTIAL_BACKENDS_KEY = "PARTIAL_BACKENDS"
_VERSION_OPTION = "VERSION"
_MANIFEST_VERSION = "manifest"
_DEFAULT_VERSION = "0"
_HASH_WIDTH = 12


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

    def version(self) -> str:
        """Return the asset version string stamped on a partial response.

        An explicit `VERSION` option wins so a deployment may pin the
        version to a release tag. The `"manifest"` sentinel resolves to a
        stable hash of the staticfiles manifest when the active
        staticfiles storage hashes its files, so the deploy-mismatch guard
        works out of the box. Without a manifest storage the sentinel
        falls back to a stable default and the guard never fires.
        """
        configured = self._options.get(_VERSION_OPTION, _MANIFEST_VERSION)
        if isinstance(configured, str) and configured != _MANIFEST_VERSION:
            return configured
        return _manifest_version()

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


def _manifest_version() -> str:
    """Return a stable version hash from the staticfiles manifest.

    The active staticfiles storage is read through the same proxy the
    static backend uses, so a legacy `STATICFILES_STORAGE` and a modern
    `STORAGES["staticfiles"]` both resolve. A manifest storage exposes a
    precomputed `manifest_hash` and the `hashed_files` mapping it loaded.
    The precomputed hash wins when present, otherwise the mapping is
    hashed so a storage with no recorded hash still yields a stable
    version. A non-manifest storage or one that fails to resolve has no
    version source, so the stable default keeps the sync guard silent.
    """
    try:
        is_manifest = isinstance(staticfiles_storage, ManifestFilesMixin)
    except ImproperlyConfigured:
        return _DEFAULT_VERSION
    if not is_manifest:
        return _DEFAULT_VERSION
    recorded = getattr(staticfiles_storage, "manifest_hash", "")
    if isinstance(recorded, str) and recorded:
        return recorded
    return _hash_mapping(getattr(staticfiles_storage, "hashed_files", {}))


def _hash_mapping(hashed_files: "Mapping[str, str]") -> str:
    """Return a short stable digest of the manifest path mapping."""
    payload = json.dumps(sorted(hashed_files.items()), separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:_HASH_WIDTH]


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
