"""Collector, dedup strategies, JS context policies, and placeholder slots."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from .assets import StaticAsset, default_kinds
from .serializers import JsContextSerializer, resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Collection, Hashable, Iterator, Sequence
    from pathlib import Path


logger = logging.getLogger(__name__)


HEAD_CLOSE: str = "</head>"

# Shared so a missed lookup allocates nothing and returns nothing a caller could fill.
_EMPTY: tuple[StaticAsset, ...] = ()
_NO_NOTES: tuple[object, ...] = ()


def _inline_dedup_key(asset: StaticAsset) -> tuple[str, str, str]:
    """Return the tuple key used to dedupe inline assets by body and kind."""
    return ("inline", asset.kind, asset.inline or "")


class DedupStrategy(Protocol):
    """Key-based dedup strategy, the collector ignores an asset whose key was seen."""

    def key(self, asset: StaticAsset) -> Hashable:
        """Return a hashable key identifying the asset for dedup."""
        raise NotImplementedError


class UrlDedup:
    """Dedupe inline assets by rendered body and URL-form assets by URL.

    This is the process-wide default.
    """

    def key(self, asset: StaticAsset) -> Hashable:
        """Return an inline-form key or a URL-form key based on the asset."""
        if asset.inline is not None:
            return _inline_dedup_key(asset)
        return ("url", asset.kind, asset.url)


class HashContentDedup:
    """Dedupe URL-form assets by sha256 of their disk content.

    An asset with no `source_path` falls back to URL dedup.
    """

    def __init__(self) -> None:
        """Initialise an empty per-path sha256 cache."""
        self._cache: dict[Path, str] = {}

    def key(self, asset: StaticAsset) -> Hashable:
        """Hash the asset disk contents when available, otherwise fall back."""
        if asset.inline is not None:
            return _inline_dedup_key(asset)
        if asset.source_path is None:
            return ("url", asset.kind, asset.url)
        cached = self._cache.get(asset.source_path)
        if cached is None:
            cached = hashlib.sha256(asset.source_path.read_bytes()).hexdigest()
            self._cache[asset.source_path] = cached
        return ("hash", asset.kind, cached)


class IdentityDedup:
    """Disable deduplication so every registration yields a unique key."""

    def __init__(self) -> None:
        """Initialise the monotonically increasing counter."""
        self._counter = 0

    def key(self, asset: StaticAsset) -> Hashable:
        """Return a unique incrementing key so dedup never triggers."""
        del asset
        self._counter += 1
        return ("unique", self._counter)


class JsContextPolicy(Protocol):
    """Merge strategy for the collector JS context."""

    def merge(
        self, existing: dict[str, Any], key: str, value: object
    ) -> dict[str, Any]:
        """Merge a new entry into the existing mapping and return it."""
        raise NotImplementedError


class FirstWinsPolicy:
    """Keep the first registration, so page context wins over component context."""

    def merge(
        self, existing: dict[str, Any], key: str, value: object
    ) -> dict[str, Any]:
        """Write the value only when the key is absent from existing."""
        if key not in existing:
            existing[key] = value
        return existing


class LastWinsPolicy:
    """Overwrite the previous value with the latest registration."""

    def merge(
        self, existing: dict[str, Any], key: str, value: object
    ) -> dict[str, Any]:
        """Assign the value under the key, overwriting any existing entry."""
        existing[key] = value
        return existing


class RaiseOnConflictPolicy:
    """Raise `KeyError` when the same key is registered twice."""

    def merge(
        self, existing: dict[str, Any], key: str, value: object
    ) -> dict[str, Any]:
        """Assign the value or raise when the key already exists."""
        if key in existing:
            msg = f"Duplicate JS context key: {key!r}"
            raise KeyError(msg)
        existing[key] = value
        return existing


class DeepMergePolicy:
    """Recursively merge dict values and override scalars with the latest value."""

    def merge(
        self, existing: dict[str, Any], key: str, value: object
    ) -> dict[str, Any]:
        """Recursively merge dict values or assign the new one otherwise."""
        current = existing.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            existing[key] = self._deep_merge(current, value)
        else:
            existing[key] = value
        return existing

    @classmethod
    def _deep_merge(cls, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        out = dict(a)
        for k, v in b.items():
            cur = out.get(k)
            if isinstance(cur, dict) and isinstance(v, dict):
                out[k] = cls._deep_merge(cur, v)
            else:
                out[k] = v
        return out


@dataclass(frozen=True, slots=True)
class PlaceholderSlot:
    """Binding between a `{% collect_* %}` placeholder name and its token.

    `token` is the HTML comment the matching template tag emits and injection replaces.
    """

    name: str
    token: str


class PlaceholderRegistry:
    """Mutable registry of placeholder slots, shipped empty."""

    def __init__(self) -> None:
        """Initialise an empty slot registry."""
        self._slots: dict[str, PlaceholderSlot] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Return the registration counter, which a memo compares to detect a change."""
        return self._version

    def register(self, name: str, *, token: str) -> None:
        """Register the slot under its name, a repeat with another token raises."""
        if not name:
            msg = "Slot name must be a non-empty string"
            raise ValueError(msg)
        if not token:
            msg = "Slot token must be a non-empty string"
            raise ValueError(msg)
        existing = self._slots.get(name)
        if existing is not None:
            if existing.token == token:
                return
            msg = (
                f"Slot {name!r} is already registered with token "
                f"{existing.token!r}. Cannot re-register with token {token!r}."
            )
            raise ValueError(msg)
        self._slots[name] = PlaceholderSlot(name=name, token=token)
        self._version += 1

    def get(self, name: str) -> PlaceholderSlot | None:
        """Return the slot registered under the given name or None."""
        return self._slots.get(name)

    def __iter__(self) -> Iterator[PlaceholderSlot]:
        """Iterate over registered slots in registration order."""
        return iter(self._slots.values())

    def __len__(self) -> int:
        """Return the number of registered slots."""
        return len(self._slots)


default_placeholders: PlaceholderRegistry = PlaceholderRegistry()


class JsContextPayload(NamedTuple):
    """The three parallel mappings one init payload is assembled from."""

    values: dict[str, Any]
    encoded: dict[str, str]
    serializers: dict[str, JsContextSerializer]


class StaticCollector:
    """Accumulate static asset references during a single page render."""

    def __init__(
        self,
        *,
        dedup: DedupStrategy | None = None,
        js_context_policy: JsContextPolicy | None = None,
        js_serializer: JsContextSerializer | None = None,
    ) -> None:
        """Wire up dedup, JS-context policy, and JS serializer."""
        self._dedup = dedup if dedup is not None else UrlDedup()
        self._js_policy = (
            js_context_policy if js_context_policy is not None else FirstWinsPolicy()
        )
        self._js_serializer = js_serializer
        self._seen_keys: set[Hashable] = set()
        self._buckets: dict[str, list[StaticAsset]] = {}
        self._prepend_idx: dict[str, int] = {}
        self._js_context: dict[str, Any] = {}
        self._js_context_serializers: dict[str, JsContextSerializer] = {}
        self._js_context_encoded: dict[str, str] = {}
        self._notes: dict[str, list[object]] = {}

    def add(self, asset: StaticAsset, *, prepend: bool = False) -> bool:
        """Add the asset unless its dedup key was already recorded, and report which.

        Inline assets always append, and an unregistered kind raises `KeyError`.
        """
        key = self._dedup.key(asset)
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        slot = default_kinds.slot(asset.kind)
        bucket = self._buckets.setdefault(slot, [])
        is_inline = asset.inline is not None
        use_prepend = prepend and not is_inline
        if use_prepend:
            idx = self._prepend_idx.get(slot, 0)
            bucket.insert(idx, asset)
            self._prepend_idx[slot] = idx + 1
        else:
            bucket.append(asset)
        return True

    def assets_in_slot(self, name: str) -> Sequence[StaticAsset]:
        """Return collected assets for the named slot in insertion order."""
        return self._buckets.get(name, _EMPTY)

    def note(self, key: str, value: object) -> None:
        """Append `value` under `key` for another area to read at injection time."""
        self._notes.setdefault(key, []).append(value)

    def notes(self, key: str) -> Sequence[object]:
        """Return what the render noted under `key`, in the order it was noted."""
        return self._notes.get(key, _NO_NOTES)

    def _get_js_serializer(self) -> JsContextSerializer:
        if self._js_serializer is None:
            self._js_serializer = resolve_serializer()
        return self._js_serializer

    def add_js_context(
        self, key: str, value: object, *, serializer: JsContextSerializer | None = None
    ) -> None:
        """Merge the value under the key through the JS-context policy.

        Validating here puts the registration site in the traceback.
        """
        active = serializer if serializer is not None else self._get_js_serializer()
        try:
            encoded = active.dumps(value)
        except (TypeError, ValueError) as e:
            msg = f"JS context value for key {key!r} is not serialisable: {e}"
            raise TypeError(msg) from e
        self._js_context = self._js_policy.merge(self._js_context, key, value)
        if self._js_context.get(key) is value:
            self._js_context_encoded[key] = encoded
        else:
            self._js_context_encoded.pop(key, None)
        if serializer is not None:
            self._js_context_serializers[key] = serializer

    def js_context(self) -> dict[str, Any]:
        """Return the accumulated JS context.

        Callers must not mutate the returned mapping.
        """
        return self._js_context

    def js_context_serializers(self) -> dict[str, JsContextSerializer]:
        """Return the per-key serializer overrides recorded so far.

        Empty when every key uses the global serializer, and callers must not mutate it.
        """
        return self._js_context_serializers

    def _encoded_fragment(
        self, key: str, value: object, default: JsContextSerializer
    ) -> str:
        """Return the cached JSON fragment for a key or re-encode on miss."""
        encoded = self._js_context_encoded.get(key)
        if encoded is None:
            serializer = self._js_context_serializers.get(key, default)
            encoded = serializer.dumps(value)
        return encoded

    def js_context_encoded(self) -> dict[str, str]:
        """Return the merged js-context as per-key encoded JSON fragments."""
        default = self._get_js_serializer()
        return {
            key: self._encoded_fragment(key, value, default)
            for key, value in self._js_context.items()
        }

    def js_context_payload(self, *, reserved: Collection[str] = ()) -> JsContextPayload:
        """Return the js-context mappings with every reserved key left out."""
        values = self._js_context
        serializers = self._js_context_serializers
        if not values:
            return JsContextPayload(values, {}, serializers)
        collided = {key for key in values if key in reserved}
        if not collided:
            return JsContextPayload(values, self.js_context_encoded(), serializers)
        default = self._get_js_serializer()
        return JsContextPayload(
            {k: v for k, v in values.items() if k not in collided},
            {
                key: self._encoded_fragment(key, value, default)
                for key, value in values.items()
                if key not in collided
            },
            {k: v for k, v in serializers.items() if k not in collided},
        )

    def js_context_wire(self) -> dict[str, Any]:
        """Return the merged js-context as wire-ready JSON values."""
        default = self._get_js_serializer()
        return {
            key: json.loads(self._encoded_fragment(key, value, default))
            for key, value in self._js_context.items()
        }
