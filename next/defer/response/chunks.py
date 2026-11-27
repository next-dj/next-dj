"""The wire mappings and chunk scripts of settled pieces, each asset sent once."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from django.conf import settings

from next.boundaries.calls import Addressing
from next.boundaries.failures import DebugBoundaryFailure, ErrorKind
from next.boundaries.ids import response_id_of
from next.defer.markers import Added, Merged
from next.diagnostics import FailureLog
from next.partial import keys
from next.partial.envelope import Patch
from next.partial.patches import Patches
from next.partial.render import ZoneRenderResult
from next.static.collector import StaticCollector, default_placeholders
from next.static.runtime import BUNDLE_NOTES

from .markup import closed, strip, wrappers


if TYPE_CHECKING:
    from django.http import HttpRequest

    from next.defer.markers import Delta, Piece
    from next.partial.envelope import Envelope
    from next.static.assets import StaticAsset
    from next.static.runtime import NextScriptBuilder


failure_log = FailureLog(logging.getLogger("next.defer"))

_NOINDEX: Final = "noindex"
_META: Final = "meta"
_ROBOTS: Final = "robots"
_TEMPLATE_HEAD: Final = "nx-"
_INDEXED_KINDS: Final = frozenset({ErrorKind.NOT_FOUND, ErrorKind.FORBIDDEN})
_NOTED: Final = (
    "A streamed deferred job noted %r for the script injection, which ran with the "
    "shell, so the page never reads it. Render the component inline there, "
    "defer='inline' at the call site."
)


def _sent_key(asset: StaticAsset) -> tuple[str, str, str | None]:
    return asset.kind, asset.url, asset.inline


def _lifted(data: dict[str, object], ident: str) -> str | None:
    """Pop and return the HTML of the op settling `ident`, `None` when none holds it."""
    envelope = data[keys.CHUNK_ENVELOPE]
    ops = envelope.get(keys.OPS, ()) if isinstance(envelope, dict) else ()
    for op in ops:
        if op.get(keys.TARGET) == {keys.BOUNDARY: ident} and keys.HTML in op:
            return str(op.pop(keys.HTML))
    return None


def _inner(tag: str, element: str) -> str:
    """Return the content of `element`, a wrapper of `tag` with escaped attributes."""
    return element[element.index(">") + 1 : len(element) - len(f"</{tag}>")]


class Payloads:
    """Turn the settled jobs of one stream into the wire mappings it writes.

    The ledger holds every asset the shell or an earlier write carried, so none repeats.
    """

    def __init__(
        self,
        request: HttpRequest,
        *,
        shell: dict[str, tuple[str | None, str]],
        collector: StaticCollector | None,
    ) -> None:
        """Hold the wrappers of the shell and the assets it already carries."""
        self._request = request
        self._wrappers = dict(shell)
        self._debug = bool(settings.DEBUG)
        self._sent: set[tuple[str, str, str | None]] = set()
        if collector is not None:
            for slot in default_placeholders:
                self._sent.update(map(_sent_key, collector.assets_in_slot(slot.name)))

    def payload(self, piece: Piece) -> dict[str, object]:
        """Return the wire mapping of one chunk, its envelope built as any partial's."""
        rendered = piece.rendered
        data: dict[str, object] = {
            keys.CHUNK_BOUNDARY: piece.id,
            keys.CHUNK_STATE: rendered.state.value,
        }
        if rendered.kind is not None:
            data[keys.CHUNK_KIND] = rendered.kind.value
            if isinstance(rendered.failure, DebugBoundaryFailure):
                data[keys.CHUNK_DIGEST] = rendered.failure.digest
        if piece.parent is not None:
            data[keys.CHUNK_PARENT] = piece.parent
        data[keys.CHUNK_ENVELOPE] = self._envelope(piece).as_dict()
        if self._debug:
            timing = piece.timing
            placement = timing.placement
            data[keys.CHUNK_DEBUG] = {
                "lane": None if timing.lane is None else timing.lane.value,
                "placement": None if placement is None else placement.reason.value,
                "queue_ms": round(timing.waited * 1000, 3),
                "run_ms": round(timing.ran * 1000, 3),
            }
        return data

    def _envelope(self, piece: Piece) -> Envelope:
        rendered = piece.rendered
        ident = piece.id
        response_id = response_id_of(ident)
        self._wrappers.update(wrappers(rendered.html, response_id))
        body = strip(rendered.html, response_id)
        tag, attributes = self._wrappers.get(ident, ("div", ""))
        element = closed(
            tag, attributes, body, rendered.kind, rendered.failure, rendered.retry
        )
        patches = Patches(self._request)
        target = {keys.BOUNDARY: ident}
        if tag is None:
            patches.replace(target, element)
        else:
            patches.inner(target, _inner(tag, element))
        patches.absorb_zone_result(
            ZoneRenderResult(html={}, bodies={}, collector=self._fresh(rendered.delta))
        )
        envelope = patches.envelope()
        if (
            piece.job.site.addressing is Addressing.PAGE
            and rendered.kind in _INDEXED_KINDS
        ):
            meta = Patch(op=_META, extras={_ROBOTS: _NOINDEX})
            envelope = replace(envelope, ops=(*envelope.ops, meta))
        self._wrappers.pop(ident, None)
        return envelope

    def _fresh(self, delta: Delta) -> StaticCollector:
        """Return a collector of what `delta` adds that no earlier write carried."""
        collector = StaticCollector()
        for step in delta.steps:
            if isinstance(step, Added):
                key = _sent_key(step.asset)
                if key not in self._sent:
                    self._sent.add(key)
                    collector.add(step.asset, prepend=step.prepend)
            elif isinstance(step, Merged):
                collector.add_js_context(
                    step.key, step.value, serializer=step.serializer
                )
            elif step.key not in BUNDLE_NOTES:
                failure_log.warn(("noted", step.key), _NOTED, step.key)
        return collector


class Chunks(Payloads):
    """Turn the settled jobs of one page stream into the chunks it writes.

    A chunk is the HTML of its boundary in an inert `<template>`, which the browser
    parses once as it streams, and a script pushing the rest, the template named in it.
    """

    def __init__(
        self,
        request: HttpRequest,
        *,
        shell: dict[str, tuple[str | None, str]],
        collector: StaticCollector | None,
        builder: NextScriptBuilder,
        nonce: str | None,
    ) -> None:
        """Hold the payloads of the stream and the script builder of its page."""
        super().__init__(request, shell=shell, collector=collector)
        self._builder = builder
        self._nonce = nonce

    def end(self) -> str:
        """Return the script marking that no chunk follows."""
        return self._builder.chunk_script(json.dumps(keys.CHUNK_END), nonce=self._nonce)

    def script(self, piece: Piece) -> str:
        """Return the template and the chunk script of one piece."""
        data = self.payload(piece)
        html = _lifted(data, piece.id)
        template = ""
        if html is not None:
            name = f"{_TEMPLATE_HEAD}{piece.id}"
            data[keys.CHUNK_TEMPLATE] = name
            template = f'<template id="{name}">{html}</template>'
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return template + self._builder.chunk_script(payload, nonce=self._nonce)


__all__ = ["Chunks", "Payloads", "failure_log"]
