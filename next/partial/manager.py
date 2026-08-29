"""Facade owning the protocol-backend manager and version resolution."""

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


_PARTIAL_BACKENDS_KEY = "PARTIAL_BACKENDS"
_VERSION_OPTION = "VERSION"
_MANIFEST_VERSION = "manifest"
_DEFAULT_VERSION = "0"
_HASH_WIDTH = 12
_DEFAULT_BACKEND_PATH = (
    f"{PartialProtocolBackend.__module__}.{PartialProtocolBackend.__qualname__}"
)
# The settings the resolved version reads through, beyond NEXT_FRAMEWORK.
_STORAGE_SETTINGS = frozenset({"STORAGES", "STATIC_ROOT"})


# PARTIAL_BACKENDS is a list, but one protocol is active (next.W071).
partial_backend_manager = SingleBackendManager(
    _PARTIAL_BACKENDS_KEY, base=PartialProtocolBackend, default=_DEFAULT_BACKEND_PATH
)


# A single-slot holder mutated in place, so a reset needs no `global`.
_ASSET_VERSION_CACHE: dict[str, str | None] = {"value": None}


def asset_version() -> str:
    """Return the memoised asset version stamped on a partial response.

    Every partial response reads it and the manifest branch hashes the whole
    path mapping, so it resolves once per configuration, not per request.
    """
    cached = _ASSET_VERSION_CACHE["value"]
    if cached is None:
        cached = _resolve_asset_version()
        _ASSET_VERSION_CACHE["value"] = cached
    return cached


def _resolve_asset_version() -> str:
    """Resolve the asset version from the backend options and the manifest.

    An explicit `VERSION` option wins so a deployment may pin the version
    to a release tag. The `"manifest"` sentinel resolves to a stable hash
    of the staticfiles manifest when the active staticfiles storage hashes
    its files, so the deploy-mismatch guard works out of the box. Without
    a manifest storage the sentinel falls back to a stable default and the
    guard never fires.
    """
    options = partial_backend_manager.get().options
    configured = options.get(_VERSION_OPTION, _MANIFEST_VERSION)
    if isinstance(configured, str) and configured != _MANIFEST_VERSION:
        return configured
    return _manifest_version()


def _manifest_version() -> str:
    """Return a stable version hash from the staticfiles manifest.

    The active staticfiles storage is read through the same proxy the
    static backend uses, so the `STORAGES["staticfiles"]` backend resolves.
    A manifest storage exposes a precomputed `manifest_hash` and the
    `hashed_files` mapping it loaded.
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


def _on_settings_reloaded(**kwargs) -> None:
    """Drop the cached backend and version so a reloaded config takes effect."""
    partial_backend_manager.reset()
    _ASSET_VERSION_CACHE["value"] = None


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memoised version when the staticfiles configuration moves.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half, and the
    storage behind the manifest hash is configured on the Django half.
    """
    if setting in _STORAGE_SETTINGS:
        _ASSET_VERSION_CACHE["value"] = None


settings_reloaded.connect(_on_settings_reloaded)
setting_changed.connect(_on_setting_changed)


__all__ = ["asset_version", "partial_backend_manager"]
