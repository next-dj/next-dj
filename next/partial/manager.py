"""Facade owning the protocol-backend manager and version resolution."""

import functools
import hashlib
import json
from typing import TYPE_CHECKING

from django.contrib.staticfiles.storage import ManifestFilesMixin, staticfiles_storage
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed

from next.backends import SingleBackendManager
from next.conf.signals import settings_reloaded

from .backends import PartialProtocolBackend


if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any


PARTIAL_BACKENDS_KEY = "PARTIAL_BACKENDS"
VERSION_OPTION = "VERSION"
MANIFEST_VERSION = "manifest"
_DEFAULT_VERSION = "0"
_HASH_WIDTH = 12
_DEFAULT_BACKEND_PATH = "next.partial.JsonPartialProtocolBackend"
# The settings the resolved version reads through, beyond NEXT_FRAMEWORK.
_STORAGE_SETTINGS = frozenset({"STORAGES", "STATIC_ROOT"})


# PARTIAL_BACKENDS is a list, but one protocol is active (next.W071).
partial_backend_manager = SingleBackendManager(
    PARTIAL_BACKENDS_KEY, base=PartialProtocolBackend, default=_DEFAULT_BACKEND_PATH
)


@functools.cache
def asset_version() -> str:
    """Return the memoised asset version stamped on a partial response.

    Every partial response reads it and the manifest branch hashes the whole
    path mapping, so it resolves once per configuration, not per request.
    """
    return _resolve_asset_version()


def pinned_version(options: "Mapping[str, Any]") -> str | None:
    """Return the release tag the options pin, or None when they ask the manifest.

    The runtime and the `next.W069` check read one predicate, so a renamed option
    cannot leave the check silently agreeing with nothing.
    """
    configured = options.get(VERSION_OPTION, MANIFEST_VERSION)
    if isinstance(configured, str) and configured != MANIFEST_VERSION:
        return configured
    return None


def _resolve_asset_version() -> str:
    """Resolve the asset version from the backend options and the manifest.

    An explicit `VERSION` option pins a release tag, and the `"manifest"` sentinel
    hashes the staticfiles manifest, falling back to a stable default without one.
    """
    pinned = pinned_version(partial_backend_manager.get().options)
    if pinned is not None:
        return pinned
    return _manifest_version()


def _manifest_version() -> str:
    """Return a stable version hash from the staticfiles manifest.

    The precomputed `manifest_hash` wins, otherwise the `hashed_files` mapping is
    hashed, and a storage exposing neither falls back to the stable default.
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


def _on_settings_reloaded(**kwargs) -> None:
    """Drop the cached backend and version so a reloaded config takes effect."""
    partial_backend_manager.reset()
    asset_version.cache_clear()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memoised version when the staticfiles configuration moves.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half, and the
    storage behind the manifest hash is configured on the Django half.
    """
    if setting in _STORAGE_SETTINGS:
        asset_version.cache_clear()


settings_reloaded.connect(_on_settings_reloaded)
setting_changed.connect(_on_setting_changed)


__all__ = ["asset_version", "partial_backend_manager", "pinned_version"]
