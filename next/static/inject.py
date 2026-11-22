"""Render collected assets into the placeholder tokens of a finished page."""

from __future__ import annotations

import logging
import re
import secrets
from typing import TYPE_CHECKING, Any, Final, Protocol

from django.conf import settings

from next.diagnostics import FailureLog
from next.ports import page_scripts_slot

from .assets import default_kinds
from .collector import HEAD_CLOSE, default_placeholders
from .nonce import resolve_nonce
from .runtime import (
    BUNDLE_NOTE,
    CHUNKS_PAYLOAD_KEY,
    CSRF_PAYLOAD_KEY,
    DEV_CHUNK_KEY,
    DEV_PAYLOAD_KEY,
    HISTORY_CHUNK_KEY,
    INTENT_CHUNK_KEY,
    LISTED_CHUNK_KEYS,
    PAGE_ASSET_ATTR,
    READER_CHUNK_KEY,
    RESERVED_PAYLOAD_KEYS,
    ROUTER_CHUNK_KEY,
    URGENT_NOTE,
    WS_CHUNK_KEY,
    BundleLoad,
    RouterRender,
    ScriptInjectionPolicy,
    bundle_load,
    csrf_payload_for,
    html_finish,
    nonce_attr,
    partial_streams,
    progress_tag,
    realtime_offer,
    router_render,
    speculation_tag,
)
from .serializers import JsContextSerializer, resolve_serializer
from .signals import collector_finalized, html_injected


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from django.http import HttpRequest

    from .assets import StaticAsset
    from .backends import StaticBackend
    from .collector import PlaceholderSlot, StaticCollector
    from .runtime import NextScriptBuilder


_RUNTIME_SLOT_NAME: Final = "scripts"
_HEAD_SLOT_NAME: Final = "head"
_NO_SCRIPTS: tuple[str, Mapping[str, object]] = ("", {})
_DROPPED: Final = (
    "A render of %s needs the bundles %s, which no tag declares, as the script "
    "policy injects no runtime or the layout holds no {%% collect_scripts %%}."
)

logger = logging.getLogger(__name__)
_dropped = FailureLog(logger)
_MARK_BYTES: Final = 16
_BODY_CLOSE: Final = "</body>"
_STYLESHEET: Final = "link"
_OPENING: Final = re.compile(r"<[a-zA-Z][a-zA-Z0-9-]*")

SPLIT_NOTE: Final = "next:split"
"""The note a streamed render asks for the split under, answered by `split_of`."""

_SPLIT_AT: Final = "next:split-at"

# Storage URLs of the listed chunks, page URLs, serializer type and encoded entry.
type _ChunksMemo = tuple[
    tuple[tuple[str, str], ...], dict[str, str], type[JsContextSerializer], str
]


class InjectionProvider(Protocol):
    """Contract the placeholder injector reads the pipeline through.

    The static manager implements it, and it is the sender `html_injected` carries.
    """

    @property
    def default_backend(self) -> StaticBackend:
        """Return the backend rendering the tags of this pipeline."""
        raise NotImplementedError

    def asset_url(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return an asset URL as the backend rewrites it for this request."""
        raise NotImplementedError

    def script_builder(self) -> NextScriptBuilder:
        """Return the builder holding the runtime URL and the tag templates."""
        raise NotImplementedError

    def chunk_url(self, name: str) -> str | None:
        """Return the URL of the lazy chunk `name`, or None if the storage lacks it."""
        raise NotImplementedError

    @property
    def rewrites_urls(self) -> bool:
        """Return whether `asset_url` may answer differently for another request."""
        raise NotImplementedError


class _Render:
    """The per-injection values computed once before the slots render."""

    __slots__ = (
        "bundles",
        "declares",
        "head",
        "payload",
        "request",
        "routed",
        "runtime_url",
    )

    def __init__(
        self, request: HttpRequest | None, runtime_url: str | None, *, declares: bool
    ) -> None:
        """Hold the request, the runtime URL and whether bundle tags may render.

        Under `manual` the page loads the runtime itself, so bundle tags still render.
        """
        self.request = request
        self.runtime_url = runtime_url
        self.declares = declares
        self.head, self.payload = _NO_SCRIPTS
        self.routed: RouterRender | None = router_render.get()
        self.bundles: Sequence[object] = ()

    def nonce(self) -> str | None:
        """Return the request nonce, resolved by the first tag that carries it.

        A minted nonce makes the response private, so a tagless render stays shareable.
        """
        return resolve_nonce(self.request)


class PlaceholderInjector:
    """Replace placeholder tokens with the tags a collector accumulated."""

    def __init__(self, provider: InjectionProvider) -> None:
        """Bind the pipeline the rendered tags and URLs are read from."""
        self._provider = provider
        self._chunks: _ChunksMemo | None = None

    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path | None = None,
        request: HttpRequest | None = None,
    ) -> str:
        """Replace every registered placeholder token with rendered tags.

        Every URL passes through `asset_url` first, so a per-request rewrite applies.
        """
        sender = self._provider
        collector_finalized.send(sender=collector, page_path=page_path, request=request)
        html_before = html
        replaced: tuple[str, ...] | None = None
        if html_injected.has_listeners(sender):
            replaced = tuple(
                slot.name for slot in default_placeholders if slot.token in html
            )
        # The runtime loads only from the scripts placeholder, so only it gets a hint.
        runtime_slot = default_placeholders.get(_RUNTIME_SLOT_NAME)
        hints_runtime = runtime_slot is not None and runtime_slot.token in html
        backend = sender.default_backend
        builder = sender.script_builder()
        # Resolved once so the script tag and the preload hint share one URL.
        runtime_url = (
            sender.asset_url(builder.url, request=request)
            if builder.policy is ScriptInjectionPolicy.AUTO
            else None
        )
        render = _Render(
            request,
            runtime_url,
            declares=builder.policy is not ScriptInjectionPolicy.DISABLED,
        )
        scripts = page_scripts_slot.peek()
        if scripts is not None:
            render.head, render.payload = scripts.render(
                collector, page_path=page_path, request=request, nonce=render.nonce
            )
        routed = render.routed
        injects = runtime_url is not None and hints_runtime
        if routed is not None:
            _route(routed, render, collector, sender)
        if injects and partial_streams.get():
            collector.require(READER_CHUNK_KEY)
        render.bundles = collector.notes(BUNDLE_NOTE)
        mark = _split_mark() if collector.notes(SPLIT_NOTE) else None
        html = self._fill(html, collector, (backend, builder), render, mark)
        if runtime_url is not None and hints_runtime:
            html = self._inject_preload_hint(html, builder, runtime_url, render.nonce())
        finish = html_finish.get() if routed is None else routed.finish
        if finish is not None:
            html = finish(html)
        if mark:
            html = _split(html, collector, mark)
        if render.bundles and not (render.declares and hints_runtime):
            _note_dropped(collector, page_path)
        if replaced is not None:
            html_injected.send(
                sender=sender,
                html_before=html_before,
                html_after=html,
                collector=collector,
                placeholders_replaced=replaced,
                injected_bytes=len(html) - len(html_before),
                request=request,
            )
        return html

    def _fill(
        self,
        html: str,
        collector: StaticCollector,
        pipeline: tuple[StaticBackend, NextScriptBuilder],
        render: _Render,
        mark: str | None,
    ) -> str:
        """Return `html` with every placeholder filled, sealed for a routed render.

        The split mark stays inside the sealed scripts, out of every chrome digest.
        """
        backend, builder = pipeline
        routed = render.routed
        for slot in default_placeholders:
            raw = self._render_slot(slot, collector, backend, builder, render)
            rendered = raw if routed is None or not raw else routed.sealed(raw)
            if slot.name == _HEAD_SLOT_NAME and rendered and slot.token not in html:
                html = html.replace(HEAD_CLOSE, f"{rendered}\n{HEAD_CLOSE}", 1)
            elif mark and slot.name == _RUNTIME_SLOT_NAME and slot.token in html:
                last = rendered + mark if routed is None else routed.sealed(raw + mark)
                html = _marked(html, slot.token, rendered, last)
            else:
                html = html.replace(slot.token, rendered)
        return html

    def _render_slot(
        self,
        slot: PlaceholderSlot,
        collector: StaticCollector,
        backend: StaticBackend,
        builder: NextScriptBuilder,
        render: _Render,
    ) -> str:
        assets = collector.assets_in_slot(slot.name)
        bundles = ""
        if slot.name == _RUNTIME_SLOT_NAME and (urls := render.bundles):
            assets, bundles = self._declared(assets, urls, collector, builder, render)
        user_tags = self._render_tags(assets, backend, render)
        if slot.name == _HEAD_SLOT_NAME and render.head:
            return f"{render.head}\n{user_tags}" if user_tags else render.head
        if slot.name == _RUNTIME_SLOT_NAME and render.runtime_url is not None:
            return self._wrap_with_runtime(
                user_tags, collector, builder, render, bundles
            )
        return bundles + user_tags if bundles else user_tags

    def _declared(
        self,
        assets: Sequence[StaticAsset],
        urls: Sequence[object],
        collector: StaticCollector,
        builder: NextScriptBuilder,
        render: _Render,
    ) -> tuple[list[StaticAsset], str]:
        """Return the scripts without the needed bundles and the tags declaring them.

        Under `disabled` no tag renders, as each bundle registers with the runtime.
        """
        wanted = dict.fromkeys(url for url in urls if isinstance(url, str))
        kept = [
            asset
            for asset in assets
            if asset.inline is not None or asset.url not in wanted
        ]
        if not render.declares:
            return kept, ""
        urgent = bool(collector.notes(URGENT_NOTE))
        streams = bool(collector.notes(SPLIT_NOTE))
        reader = _reader_url(collector, streams=streams)
        router = _router_url(collector, render)
        nonce = render.nonce()
        request = render.request
        tags = "".join(
            builder.bundle_tag(
                self._provider.asset_url(url, request=request),
                load=BundleLoad.EAGER
                if url == router
                else bundle_load(reader=url == reader, streams=streams, urgent=urgent),
                nonce=nonce,
            )
            + "\n"
            for url in wanted
        )
        return kept, tags

    def _reserved_payload(self, render: _Render, *, dev: bool) -> dict[str, Any]:
        """Return the framework-owned init-payload entries for this render."""
        payload: dict[str, Any] = {}
        csrf = csrf_payload_for(render.request)
        if csrf is not None:
            payload[CSRF_PAYLOAD_KEY] = csrf
        if dev:
            payload[DEV_PAYLOAD_KEY] = True
        payload.update(render.payload)
        return payload

    def _chunks_entry(
        self, request: HttpRequest | None, *, dev: bool
    ) -> tuple[dict[str, str], str]:
        """Return the `$chunks` entry and its encoding, reused while the URLs match.

        Only `DEBUG` lists the dev chunk, and a chunk the storage lacks is left out.
        """
        provider = self._provider
        websocket = realtime_offer().websocket
        stored = tuple(
            (name, url)
            for name in LISTED_CHUNK_KEYS
            if (dev or name != DEV_CHUNK_KEY)
            and (websocket or name != WS_CHUNK_KEY)
            and (url := provider.chunk_url(name))
        )
        serializer = resolve_serializer()
        held = self._chunks
        if held is not None and held[0] == stored and not provider.rewrites_urls:
            urls = held[1]
        else:
            urls = {
                name: provider.asset_url(url, request=request) for name, url in stored
            }
        if held is not None and held[1] == urls and held[2] is type(serializer):
            return urls, held[3]
        fragment = serializer.dumps(urls)
        self._chunks = (stored, urls, type(serializer), fragment)
        return urls, fragment

    def _wrap_with_runtime(
        self,
        user_tags: str,
        collector: StaticCollector,
        builder: NextScriptBuilder,
        render: _Render,
        bundles: str,
    ) -> str:
        payload = collector.js_context_payload(reserved=RESERVED_PAYLOAD_KEYS)
        dev = bool(settings.DEBUG)
        chunks, chunks_encoded = self._chunks_entry(render.request, dev=dev)
        js_context = {
            **payload.values,
            **self._reserved_payload(render, dev=dev),
            CHUNKS_PAYLOAD_KEY: chunks,
        }
        encoded = {**payload.encoded, CHUNKS_PAYLOAD_KEY: chunks_encoded}
        init_payload = builder.init_script(
            js_context,
            key_serializers=payload.serializers,
            encoded=encoded,
            nonce=render.nonce(),
        )
        tag = builder.script_tag(render.runtime_url, nonce=render.nonce())
        next_scripts = f"{tag}\n{bundles}{init_payload}\n"
        return next_scripts + user_tags if user_tags else next_scripts

    def _inject_preload_hint(
        self, html: str, builder: NextScriptBuilder, runtime_url: str, nonce: str | None
    ) -> str:
        replacement = f"{builder.preload_link(runtime_url, nonce=nonce)}\n{HEAD_CLOSE}"
        return html.replace(HEAD_CLOSE, replacement, 1)

    def _render_tags(
        self, assets: Sequence[StaticAsset], backend: StaticBackend, render: _Render
    ) -> str:
        if not assets:
            return ""
        return "\n".join([self._render_one(asset, backend, render) for asset in assets])

    def _render_one(
        self, asset: StaticAsset, backend: StaticBackend, render: _Render
    ) -> str:
        rendered = self._tag(asset, backend, render)
        if render.routed is None or (
            default_kinds.load(asset.kind, inline=asset.inline is not None)
            != _STYLESHEET
        ):
            return rendered
        return _owned(rendered)

    def _tag(self, asset: StaticAsset, backend: StaticBackend, render: _Render) -> str:
        if asset.inline is not None:
            tag = default_kinds.inline_tag(asset.kind)
            if tag is None:
                return asset.inline
            return f"<{tag}{nonce_attr(render.nonce())}>{asset.inline}</{tag}>"
        renderer = getattr(backend, default_kinds.renderer(asset.kind))
        url = self._provider.asset_url(asset.url, request=render.request)
        nonce = render.nonce()
        # A renderer written without the nonce keyword still serves a page without one.
        rendered: str = (
            renderer(url, request=render.request)
            if nonce is None
            else renderer(url, request=render.request, nonce=nonce)
        )
        return rendered


def _owned(tag: str) -> str:
    """Return `tag` with the page asset attribute after its first element name.

    The client router disables a marked stylesheet the next page does not list.
    """
    opening = _OPENING.search(tag)
    if opening is None:
        return tag
    at = opening.end()
    return f"{tag[:at]}{PAGE_ASSET_ATTR}{tag[at:]}"


def _route(
    routed: RouterRender,
    render: _Render,
    collector: StaticCollector,
    provider: InjectionProvider,
) -> None:
    """Hand a routed render its collector, scripts payload, head extras and bundles.

    The reader serves a streamed navigation, the history and intent load once idle.
    """
    routed.collector = collector
    routed.payload = render.payload
    extras: list[str] = []
    if routed.progress:
        extras.append(progress_tag(render.nonce()))
    if routed.speculation:
        extras.append(speculation_tag(routed.speculation, render.nonce()))
    if render.head:
        extras.append(render.head)
    render.head = "\n".join(extras)
    collector.require(ROUTER_CHUNK_KEY)
    resolve = collector.bundle_url
    if resolve is None:
        return
    reader = resolve(READER_CHUNK_KEY)
    if reader is not None:
        routed.reader = provider.asset_url(reader, request=render.request)
    history = resolve(HISTORY_CHUNK_KEY)
    if history is not None:
        routed.history = provider.asset_url(history, request=render.request)
    intent = resolve(INTENT_CHUNK_KEY)
    if intent is not None:
        routed.intent = provider.asset_url(intent, request=render.request)


def _reader_url(collector: StaticCollector, *, streams: bool) -> str | None:
    """Return the URL of the stream reader where the page requires it, else `None`.

    Only a streamed page or one marked for streamed partials requires the reader.
    """
    resolve = collector.bundle_url
    if resolve is None or not (streams or partial_streams.get()):
        return None
    return resolve(READER_CHUNK_KEY)


def _router_url(collector: StaticCollector, render: _Render) -> str | None:
    """Return the URL of the router bundle a routed render requires, else `None`.

    It loads as a plain `async` script, since the first click needs it.
    """
    resolve = collector.bundle_url
    if resolve is None or render.routed is None:
        return None
    return resolve(ROUTER_CHUNK_KEY)


def _note_dropped(collector: StaticCollector, page_path: Path | None) -> None:
    """Log at a bounded rate that the bundles a render needs reach no tag."""
    urls = ", ".join(str(url) for url in collector.notes(BUNDLE_NOTE))
    _dropped.emit(logging.DEBUG, page_path, _DROPPED, (page_path, urls))


def _split_mark() -> str:
    """Return a mark no page text can hold, fresh for each render."""
    return f"\x00next-split-{secrets.token_hex(_MARK_BYTES)}\x00"


def _marked(html: str, token: str, rendered: str, last: str) -> str:
    """Return `html` with every `token` rendered, the last one as `last`.

    `last` carries the split mark, inside the sealed comments of a routed render.
    """
    at = html.rfind(token)
    head = html[:at].replace(token, rendered)
    return f"{head}{last}{html[at + len(token) :]}"


def _split(html: str, collector: StaticCollector, mark: str) -> str:
    """Return `html` without `mark`, noting the split offset.

    It is the end of the scripts slot, or the last `</body>` when that comes later, so
    chunks always follow every boundary wrapper, and the end without either.
    """
    at = html.find(mark)
    if at >= 0:
        html = html[:at] + html[at + len(mark) :]
    body = html.rfind(_BODY_CLOSE)
    split = max(at, body)
    collector.note(_SPLIT_AT, len(html) if split < 0 else split)
    return html


def split_of(collector: StaticCollector) -> int | None:
    """Return the offset the scripts slot of an injected page ends at, if it was asked.

    A streamed page writes its shell up to there, its chunks, then the rest.
    """
    noted = collector.notes(_SPLIT_AT)
    found = noted[-1] if noted else None
    return found if isinstance(found, int) else None


__all__ = ["SPLIT_NOTE", "InjectionProvider", "PlaceholderInjector", "split_of"]
