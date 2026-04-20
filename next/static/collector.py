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
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .assets import _KIND_CSS, _KIND_JS, StaticAsset


if TYPE_CHECKING:
    from collections.abc import Hashable, Iterator
    from pathlib import Path


logger = logging.getLogger(__name__)


STYLES_PLACEHOLDER: str = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER: str = "<!-- next:scripts -->"
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
        ...


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
        ...


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
    """Binding between a `{% collect_* %}` placeholder and collector state.

    The `name` field is the short slot identifier used by the
    corresponding template tag. The `token` field is the HTML comment
    marker emitted into the template output. The `bucket` field is the
    attribute name on the collector that holds the asset list for this
    slot, for example `_styles`. The `renderer` field is the method
    name on the backend used to render URL-form assets for this slot,
    for example `render_link_tag`.
    """

    name: str
    token: str
    bucket: str
    renderer: str


class PlaceholderRegistry:
    """Mutable registry of placeholder slots produced by `{% collect_* %}`.

    The built-in slots `styles` and `scripts` are pre-registered on the
    default registry. Users may register new slots such as `meta` for
    `<meta>` tags by extending the registry and supplying matching
    template tags.
    """

    def __init__(self) -> None:
        """Initialise an empty slot registry."""
        self._slots: dict[str, PlaceholderSlot] = {}

    def register(self, slot: PlaceholderSlot) -> None:
        """Register the slot under its name, overwriting any previous entry."""
        self._slots[slot.name] = slot

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
default_placeholders.register(
    PlaceholderSlot(
        name="styles",
        token=STYLES_PLACEHOLDER,
        bucket="_styles",
        renderer="render_link_tag",
    )
)
default_placeholders.register(
    PlaceholderSlot(
        name="scripts",
        token=SCRIPTS_PLACEHOLDER,
        bucket="_scripts",
        renderer="render_script_tag",
    )
)


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
    """

    def __init__(
        self,
        *,
        dedup: DedupStrategy | None = None,
        js_context_policy: JsContextPolicy | None = None,
    ) -> None:
        """Wire up dedup and JS-context policies and prime empty buckets."""
        self._dedup = dedup if dedup is not None else UrlDedup()
        self._js_policy = (
            js_context_policy if js_context_policy is not None else FirstWinsPolicy()
        )
        self._seen_keys: set[Hashable] = set()
        self._styles: list[StaticAsset] = []
        self._scripts: list[StaticAsset] = []
        self._styles_prepend_idx: int = 0
        self._scripts_prepend_idx: int = 0
        self._js_context: dict[str, Any] = {}

    def add(self, asset: StaticAsset, *, prepend: bool = False) -> None:
        """Add the asset unless its dedup key was already recorded.

        Inline assets always append because their dedup key derives
        from the body. URL-form assets with `prepend=True` are inserted
        before existing append entries while keeping registration
        order among prepended items.
        """
        key = self._dedup.key(asset)
        if key in self._seen_keys:
            return
        self._seen_keys.add(key)
        is_inline = asset.inline is not None
        use_prepend = prepend and not is_inline
        if asset.kind == _KIND_CSS:
            self._insert(self._styles, asset, prepend=use_prepend, kind=_KIND_CSS)
        elif asset.kind == _KIND_JS:
            self._insert(self._scripts, asset, prepend=use_prepend, kind=_KIND_JS)
        else:
            logger.debug(
                "Ignoring asset with unknown kind %r: %s", asset.kind, asset.url
            )

    def _insert(
        self,
        target: list[StaticAsset],
        asset: StaticAsset,
        *,
        prepend: bool,
        kind: str,
    ) -> None:
        if prepend:
            idx = (
                self._styles_prepend_idx
                if kind == _KIND_CSS
                else self._scripts_prepend_idx
            )
            target.insert(idx, asset)
            if kind == _KIND_CSS:
                self._styles_prepend_idx += 1
            else:
                self._scripts_prepend_idx += 1
        else:
            target.append(asset)

    def styles(self) -> list[StaticAsset]:
        """Return collected CSS assets in insertion order.

        Callers must not mutate the returned list.
        """
        return self._styles

    def scripts(self) -> list[StaticAsset]:
        """Return collected JS assets in insertion order.

        Callers must not mutate the returned list.
        """
        return self._scripts

    def add_js_context(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Merge the value under the key through the JS-context policy."""
        self._js_context = self._js_policy.merge(self._js_context, key, value)

    def js_context(self) -> dict[str, Any]:
        """Return the accumulated JS context.

        Callers must not mutate the returned mapping.
        """
        return self._js_context
