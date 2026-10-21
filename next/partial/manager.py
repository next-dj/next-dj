"""Facade owning the protocol-backend manager and version resolution."""

import functools
import hashlib
import json
from typing import TYPE_CHECKING

from django.contrib.staticfiles.storage import ManifestFilesMixin, staticfiles_storage
from django.core.exceptions import ImproperlyConfigured

from next.backends import SingleBackendManager
from next.conf import next_framework_settings

from .backends import PartialProtocolBackend
from .signals import partial_backend_loaded


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
    PARTIAL_BACKENDS_KEY,
    base=PartialProtocolBackend,
    default=_DEFAULT_BACKEND_PATH,
    signal=partial_backend_loaded,
)


@functools.cache
def asset_version() -> str:
    """Return the memoised asset version stamped on a partial response.

    Every response reads it and the manifest branch hashes the mapping.
    """
    return _resolve_asset_version()


def pinned_version(options: "Mapping[str, Any]") -> str | None:
    """Return the release tag the options pin, or None when the version is derived."""
    configured = options.get(VERSION_OPTION)
    if isinstance(configured, str) and configured != MANIFEST_VERSION:
        return configured
    return None


def _resolve_asset_version() -> str:
    """Resolve the asset version from the options, the project stamp, and the manifest.

    A derived default reads `STATIC_VERSION` before it asks the staticfiles manifest.
    """
    options = partial_backend_manager.get().options
    pinned = pinned_version(options)
    if pinned is not None:
        return pinned
    if options.get(VERSION_OPTION) == MANIFEST_VERSION:
        return _manifest_version()
    return _project_version() or _manifest_version()


def _project_version() -> str | None:
    """Return the `STATIC_VERSION` deploy stamp, or None when the project sets none."""
    version = next_framework_settings.STATIC_VERSION
    return str(version) if version else None


def _manifest_version() -> str:
    """Return a stable version hash from the staticfiles manifest.

    `manifest_hash` wins, then a hash of `hashed_files`, else the stable default.
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


def forget_partial_backend(**kwargs) -> None:
    """Drop the cached backend and version so a reloaded config takes effect."""
    partial_backend_manager.reset()
    asset_version.cache_clear()


def forget_asset_version(*, setting: str, **kwargs) -> None:
    """Drop the memoised version when the staticfiles configuration moves.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half of the configuration.
    """
    if setting in _STORAGE_SETTINGS:
        asset_version.cache_clear()


__all__ = ["asset_version", "partial_backend_manager", "pinned_version"]
