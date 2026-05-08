"""Collector, dedup strategies, JS context policies, and placeholder slots.

Rendering flows are stateful. Every HTTP request spins up a fresh
collector that rides along in the template context, absorbs every
`{% use_style %}`, `{% #use_script %}`, co-located `template.css`, and
`styles` or `scripts` list entry, then hands the accumulated set back to
the static manager when the template finishes.

The collector does not hardcode deduplication or merge semantics.
Strategy objects plug in at construction time, so users can swap
URL-based dedup for content-hash dedup or replace the default
first-wins JS-context merge with a deep-merge policy without touching
the collector source.

The collector is also fully type-agnostic. Each asset routes to a
slot named in `KindRegistry`, and the buckets live in a slot-keyed
dictionary on the collector. There is no built-in knowledge of `css`,
`js`, or any other specific kind here.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .assets import StaticAsset, default_kinds
from .serializers import JsContextSerializer, resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Hashable, Iterator
    from pathlib import Path


logger = logging.getLogger(__name__)


HEAD_CLOSE: str = "</head>"


def _inline_dedup_key(asset: StaticAsset) -> tuple[str, str, str]:
    """Return the tuple key used to dedupe inline assets by body and kind."""
    return ("inline", asset.kind, asset.inline or "")


@runtime_checkable
class DedupStrategy(Protocol):
    """Key-based dedup strategy consumed by the static collector.

    Implementations return a hashable value that uniquely identifies an
    asset for deduplication. The collector ignores any asset whose key
    was already recorded.
    """

    def key(self, asset: StaticAsset) -> Hashable:
        """Return a hashable key identifying the asset for dedup."""
        raise NotImplementedError


class UrlDedup:
    """Dedupe inline assets by rendered body and URL-form assets by URL.

    This is the process-wide default. It mirrors the behavior of the
    original hand-rolled dedup built into the earlier collector.
    """

    def key(self, asset: StaticAsset) -> Hashable:
        """Return an inline-form key or a URL-form key based on the asset."""
        if asset.inline is not None:
            return _inline_dedup_key(asset)
        return ("url", asset.kind, asset.url)


class HashContentDedup:
    """Dedupe URL-form assets by sha256 of their disk content.

    This is useful in production builds where identical CSS may be
    emitted under different hashed filenames by a manifest storage. The
    strategy falls back to URL-based dedup when the `source_path` is
    missing.
    """

    def __init__(self) -> None:
        """Initialise an empty per-path sha256 cache."""
        self._cache: dict[Path, str] = {}

    def key(self, asset: StaticAsset) -> Hashable:
        """Hash the asset disk contents when available, otherwise fall back."""
        if asset.inline is not None:  # pragma: no cover
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

    def key(self, asset: StaticAsset) -> Hashable:  # noqa: ARG002
        """Return a unique incrementing key so dedup never triggers."""
        self._counter += 1
        return ("unique", self._counter)


@runtime_checkable
class JsContextPolicy(Protocol):
    """Merge strategy for the collector JS context."""

    def merge(
        self,
        existing: dict[str, Any],
        key: str,
        value: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Merge a new entry into the existing mapping and return it."""
        raise NotImplementedError


class FirstWinsPolicy:
    """Keep the first registration and silently ignore subsequent writes.

    This is the default policy. Page-level context wins over
    component-level context when both register the same key.
    """

    def merge(
        self,
        existing: dict[str, Any],
        key: str,
        value: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Write the value only when the key is absent from existing."""
        if key not in existing:
            existing[key] = value
        return existing


class LastWinsPolicy:
    """Overwrite the previous value with the latest registration."""

    def merge(
        self,
        existing: dict[str, Any],
        key: str,
        value: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Assign the value under the key, overwriting any existing entry."""
        existing[key] = value
        return existing


class RaiseOnConflictPolicy:
    """Raise `KeyError` when the same key is registered twice."""

    def merge(
        self,
        existing: dict[str, Any],
        key: str,
        value: Any,  # noqa: ANN401
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
        self,
        existing: dict[str, Any],
        key: str,
        value: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Recursively merge dict values or assign the new one otherwise."""
        current = existing.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            existing[key] = self._deep_merge(current, value)
        else:
            existing[key] = value
        return existing

    @classmethod
    def _deep_merge(
        cls,
        a: dict[str, Any],
        b: dict[str, Any],
    ) -> dict[str, Any]:
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

    The `name` field identifies the slot. Assets routed to this slot by
    `KindRegistry.slot(asset.kind)` accumulate in the collector under
    this name. The `token` field is the HTML comment marker emitted by
    the matching template tag at render time and replaced by the static
    manager during injection.
    """

    name: str
    token: str


class PlaceholderRegistry:
    """Mutable registry of placeholder slots.

    The registry ships empty. Framework bootstrap registers built-in
    slots such as `styles` and `scripts`, and user code registers
    additional slots with the same `register` call when introducing new
    asset destinations.
    """

    def __init__(self) -> None:
        """Initialise an empty slot registry."""
        self._slots: dict[str, PlaceholderSlot] = {}

    def register(self, name: str, *, token: str) -> None:
        """Register the slot under its name with the given placeholder token.

        A repeated call with the same token is idempotent. A repeated
        call with a different token raises `ValueError` so silent
        overrides cannot mask bugs.
        """
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


class StaticCollector:
    """Accumulate static asset references during a single page render.

    The optional `dedup` argument plugs in a custom dedup strategy. The
    default is `UrlDedup`. The optional `js_context_policy` argument
    plugs in a custom merge strategy for the JS context. The default is
    `FirstWinsPolicy`, which ensures page-level context wins over
    component-level context.

    Assets are added through the `add` method and later consumed by the
    static manager during injection. The collector has no knowledge of
    backends or rendering. It coordinates insertion order,
    deduplication, and JS context merging.

    Buckets are keyed by slot name as resolved through `KindRegistry`.
    The collector does not hardcode any specific slot, so adding new
    asset kinds to the registry transparently produces new buckets.
    """

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

    def add(self, asset: StaticAsset, *, prepend: bool = False) -> None:
        """Add the asset unless its dedup key was already recorded.

        Inline assets always append because their dedup key derives
        from the body. URL-form assets with `prepend=True` are inserted
        before existing append entries while keeping registration
        order among prepended items.

        The asset routes to the bucket named by
        `KindRegistry.slot(asset.kind)`. Unregistered kinds raise
        `KeyError` so misconfiguration surfaces immediately.
        """
        key = self._dedup.key(asset)
        if key in self._seen_keys:
            return
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

    def assets_in_slot(self, name: str) -> list[StaticAsset]:
        """Return collected assets for the named slot in insertion order.

        Returns an empty list when nothing was registered for the slot.
        Callers must not mutate the returned list.
        """
        return self._buckets.get(name, [])

    def _get_js_serializer(self) -> JsContextSerializer:
        if self._js_serializer is None:
            self._js_serializer = resolve_serializer()
        return self._js_serializer

    def add_js_context(
        self,
        key: str,
        value: Any,  # noqa: ANN401
        *,
        serializer: JsContextSerializer | None = None,
    ) -> None:
        """Merge the value under the key through the JS-context policy.

        Validates that `value` is serialisable by the active serializer
        before merging. Surfacing the failure here, at the registration
        site, gives a much better traceback than catching it at final
        page inject time. When `serializer` is supplied, the override
        validates this value and is recorded for the inject phase so
        the same key uses the same serializer end to end. The override
        does not leak into other keys.
        """
        active = serializer if serializer is not None else self._get_js_serializer()
        try:
            active.dumps(value)
        except (TypeError, ValueError) as e:
            msg = f"JS context value for key {key!r} is not serialisable: {e}"
            raise TypeError(msg) from e
        self._js_context = self._js_policy.merge(self._js_context, key, value)
        if serializer is not None:
            self._js_context_serializers[key] = serializer

    def js_context(self) -> dict[str, Any]:
        """Return the accumulated JS context.

        Callers must not mutate the returned mapping.
        """
        return self._js_context

    def js_context_serializers(self) -> dict[str, JsContextSerializer]:
        """Return the per-key serializer overrides recorded so far.

        The returned mapping is empty when every key uses the global
        serializer. Callers must not mutate it.
        """
        return self._js_context_serializers
