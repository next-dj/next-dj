"""Render collected assets into the placeholder tokens of a finished page."""

from __future__ import annotations

import logging
import re
import secrets
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Protocol

from django.conf import settings

from next.csrf import document_csrf
from next.diagnostics import FailureLog
from next.ports import PageScripts, page_scripts, stash_layer_loading

from .assets import default_kinds
from .collector import HEAD_CLOSE, default_placeholders
from .nonce import resolve_nonce
from .runtime import (
    BUNDLE_NOTE,
    CHUNKS_PAYLOAD_KEY,
    CSRF_PAYLOAD_KEY,
    DEV_CHUNK_KEY,
    DEV_PAYLOAD_KEY,
    RESERVED_PAYLOAD_KEYS,
    URGENT_NOTE,
    BundleLoad,
    ScriptInjectionPolicy,
    bundle_load,
    html_finish,
    nonce_attr,
)
from .serializers import JsContextSerializer, resolve_serializer
from .signals import collector_finalized, html_injected


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from django.http import HttpRequest

    from next.ports import ScriptShare

    from .assets import StaticAsset
    from .backends import StaticBackend
    from .collector import PlaceholderSlot, StaticCollector
    from .runtime import NextScriptBuilder


_RUNTIME_SLOT_NAME: Final = "scripts"
_DEV: Final = frozenset({DEV_CHUNK_KEY})
_HEAD_SLOT_NAME: Final = "head"
_NO_ENTRIES: Mapping[str, object] = MappingProxyType({})
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

# The provider list and its version, the providers that apply and the reserved keys.
type _Active = tuple[object, int, tuple[PageScripts, ...], frozenset[str]]


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

    def listed_chunks(self, enabled: frozenset[str]) -> tuple[tuple[str, str], ...]:
        """Return the name and storage URL of each chunk `$chunks` lists.

        An optional chunk is listed only when `enabled` names it.
        """
        raise NotImplementedError

    @property
    def rewrites_urls(self) -> bool:
        """Return whether `asset_url` may answer differently for another request."""
        raise NotImplementedError


class _Parts:
    """What the providers of one render share, each part merged in their run order.

    Enclosures and passes compose into one callable, so a render calls each once.
    """

    __slots__ = (
        "after",
        "eager",
        "enclose",
        "entries",
        "finish",
        "head",
        "listed",
        "needs",
        "shared",
        "stylesheet",
    )

    def __init__(self, shares: Sequence[ScriptShare] = ()) -> None:
        """Merge `shares` in one pass, as most of them fill one or two parts."""
        self.head = ""
        self.entries: Mapping[str, object] = _NO_ENTRIES
        self.shared: tuple[str, ...] = ()
        self.after: tuple[Callable[[str | None], str], ...] = ()
        self.eager: tuple[str, ...] = ()
        self.needs: tuple[str, ...] = ()
        self.listed: frozenset[str] = frozenset()
        self.enclose: Callable[[str], str] | None = None
        self.finish: Callable[[str], str] | None = None
        self.stylesheet = ""
        for share in shares:
            if share.head:
                self.head = f"{self.head}\n{share.head}" if self.head else share.head
            if share.entries:
                self.entries = {**self.entries, **share.entries}
            if share.shared:
                self.shared += (share.shared,)
            if share.after is not None:
                self.after += (share.after,)
            if share.eager or share.bundles or share.listed:
                self._need(share)
            if share.enclose or share.finish or share.stylesheet:
                self._pass(share)

    def _need(self, share: ScriptShare) -> None:
        """Add the bundles and the listed chunks `share` needs."""
        self.eager += share.eager
        self.needs += share.bundles
        self.listed |= share.listed

    def _pass(self, share: ScriptShare) -> None:
        """Add the enclosure, the pass and the stylesheet attributes `share` writes."""
        if share.enclose is not None:
            self.enclose = _then(self.enclose, share.enclose)
        if share.finish is not None:
            self.finish = _then(self.finish, share.finish)
        self.stylesheet += share.stylesheet


def _then(
    first: Callable[[str], str] | None, second: Callable[[str], str]
) -> Callable[[str], str]:
    """Return `second`, run after `first` when there is one."""
    return second if first is None else partial(_chained, first, second)


def _chained(
    first: Callable[[str], str], second: Callable[[str], str], html: str
) -> str:
    """Return `html` through `first`, then `second`."""
    return second(first(html))


_NO_PARTS: Final = _Parts()
_UNRESOLVED: Final = object()


class _Render:
    """The per-injection values computed once before the slots render.

    It is the `ScriptRender` every provider reads, and `parts` holds what they share.
    """

    __slots__ = (
        "_nonce",
        "_provider",
        "bundles",
        "collector",
        "declares",
        "page_path",
        "parts",
        "request",
        "reserved",
        "runtime",
        "runtime_url",
        "streams",
    )

    def __init__(
        self,
        provider: InjectionProvider,
        collector: StaticCollector,
        page_path: Path | None,
        request: HttpRequest | None,
        runtime_url: str | None,
        *,
        declares: bool,
        runtime: bool,
    ) -> None:
        """Hold the render, the runtime URL and whether bundle tags may render.

        Under `manual` the page loads the runtime itself, so bundle tags still render.
        """
        self._provider = provider
        self.collector = collector
        self.page_path = page_path
        self.request = request
        self.runtime_url = runtime_url
        self.declares = declares
        self.runtime = runtime
        self.streams = bool(collector.notes(SPLIT_NOTE))
        self.reserved = RESERVED_PAYLOAD_KEYS
        self.parts = _NO_PARTS
        self.bundles: Sequence[object] = ()
        self._nonce: object = _UNRESOLVED

    @property
    def entries(self) -> Mapping[str, object]:
        """Return the request payload entries the providers added."""
        return self.parts.entries

    def nonce(self) -> str | None:
        """Return the request nonce, resolved by the first tag that carries it.

        A minted nonce makes the response private, so a tagless render stays shareable.
        """
        held = self._nonce
        if held is _UNRESOLVED:
            held = self._nonce = resolve_nonce(self.request)
        return held if isinstance(held, str) else None

    def bundle_url(self, name: str) -> str | None:
        """Return the URL of the declared bundle `name` as this request rewrites it."""
        resolve = self.collector.bundle_url
        url = None if resolve is None else resolve(name)
        if url is None:
            return None
        return self._provider.asset_url(url, request=self.request)


class PlaceholderInjector:
    """Replace placeholder tokens with the tags a collector accumulated."""

    def __init__(self, provider: InjectionProvider) -> None:
        """Bind the pipeline the rendered tags and URLs are read from."""
        self._provider = provider
        self._chunks: _ChunksMemo | None = None
        self._csrf: tuple[tuple[object, ...], str] | None = None
        self._active: _Active | None = None

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
        html = stash_layer_loading(html, collector, request)
        collector_finalized.send(sender=collector, page_path=page_path, request=request)
        html_before = html
        spots = _spots(html)
        present = _present(spots)
        replaced: tuple[str, ...] | None = None
        if html_injected.has_listeners(sender):
            replaced = tuple(slot.name for slot in present)
        # The runtime loads only from the scripts placeholder, so only it gets a hint.
        hints_runtime = any(slot.name == _RUNTIME_SLOT_NAME for slot in present)
        backend = sender.default_backend
        builder = sender.script_builder()
        # Resolved once so the script tag and the preload hint share one URL.
        runtime_url = (
            sender.asset_url(builder.url, request=request)
            if builder.policy is ScriptInjectionPolicy.AUTO
            else None
        )
        render = _Render(
            sender,
            collector,
            page_path,
            request,
            runtime_url,
            declares=builder.policy is not ScriptInjectionPolicy.DISABLED,
            runtime=runtime_url is not None and hints_runtime,
        )
        self._share(render)
        render.bundles = collector.notes(BUNDLE_NOTE)
        mark = _split_mark() if render.streams else None
        hint = (
            builder.preload_link(runtime_url, nonce=render.nonce())
            if runtime_url is not None and hints_runtime
            else None
        )
        filled = self._filled(present, collector, (backend, builder), render, mark)
        html = _spliced(html, spots, filled, hint)
        finish = render.parts.finish or html_finish.get()
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

    def _share(self, render: _Render) -> None:
        """Gather what every applying provider adds and record the bundles they need.

        The eager bundles are needed first, so their tags open the declared ones.
        """
        active, render.reserved = self._providers()
        if not active:
            return
        shares = [share for item in active if (share := item.share(render))]
        if not shares:
            return
        parts = render.parts = _Parts(shares)
        collector = render.collector
        for name in parts.eager:
            collector.require(name)
        for name in parts.needs:
            collector.require(name)

    def _providers(self) -> tuple[tuple[PageScripts, ...], frozenset[str]]:
        """Return the providers that apply and the payload keys page context cannot use.

        Held while the list stands, and a settings reload rebuilds the manager.
        """
        listed = page_scripts
        held = self._active
        if held is not None and held[0] is listed and held[1] == listed.version:
            return held[2], held[3]
        providers = tuple(listed)
        active = tuple(item for item in providers if item.applies())
        reserved = RESERVED_PAYLOAD_KEYS.union(*(item.keys for item in providers))
        self._active = (listed, listed.version, active, reserved)
        return active, reserved

    def _filled(
        self,
        present: Sequence[PlaceholderSlot],
        collector: StaticCollector,
        pipeline: tuple[StaticBackend, NextScriptBuilder],
        render: _Render,
        mark: str | None,
    ) -> _Filled:
        """Render each slot the page holds once, through the providers' enclosure.

        A slot the page lacks renders nothing, so a page without scripts mints no token.
        """
        backend, builder = pipeline
        enclose = render.parts.enclose
        filled = _Filled()
        for slot in present:
            raw = self._render_slot(slot, collector, backend, builder, render)
            filled.tags[slot.name] = raw if enclose is None or not raw else enclose(raw)
            if mark and slot.name == _RUNTIME_SLOT_NAME:
                filled.last = raw + mark if enclose is None else enclose(raw + mark)
        head = default_placeholders.get(_HEAD_SLOT_NAME)
        if head is not None and head.name not in filled.tags:
            raw = self._render_slot(head, collector, backend, builder, render)
            if raw:
                filled.head = raw if enclose is None else enclose(raw)
        return filled

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
        if slot.name == _HEAD_SLOT_NAME and (head := render.parts.head):
            return f"{head}\n{user_tags}" if user_tags else head
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
        load = bundle_load(
            streams=render.streams, urgent=bool(collector.notes(URGENT_NOTE))
        )
        eager = _eager_urls(collector, render.parts.eager)
        nonce = render.nonce()
        request = render.request
        tags = "".join(
            builder.bundle_tag(
                self._provider.asset_url(url, request=request),
                load=BundleLoad.EAGER if url in eager else load,
                nonce=nonce,
            )
            + "\n"
            for url in wanted
        )
        return kept, tags

    def _reserved_payload(self, render: _Render, *, dev: bool) -> dict[str, Any]:
        """Return the framework-owned init-payload entries for this render."""
        payload: dict[str, Any] = {}
        csrf = document_csrf(render.request, streams=render.streams)
        if csrf is not None:
            payload[CSRF_PAYLOAD_KEY] = csrf
        if dev:
            payload[DEV_PAYLOAD_KEY] = True
        payload.update(render.parts.entries)
        return payload

    def _chunks_entry(
        self, request: HttpRequest | None, enabled: frozenset[str]
    ) -> tuple[dict[str, str], str]:
        """Return the `$chunks` entry and its encoding, reused while the URLs match.

        An optional chunk is listed once enabled, and one the storage lacks is left out.
        """
        provider = self._provider
        stored = provider.listed_chunks(enabled)
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

    def _csrf_entry(self, csrf: Mapping[str, str]) -> str:
        """Return the encoded `$csrf` entry, reused while the entry repeats.

        The endpoint form repeats on every page of a site, a token never does.
        """
        serializer = resolve_serializer()
        key = (type(serializer), *csrf.items())
        held = self._csrf
        if held is not None and held[0] == key:
            return held[1]
        fragment = serializer.dumps(csrf)
        self._csrf = (key, fragment)
        return fragment

    def _wrap_with_runtime(
        self,
        user_tags: str,
        collector: StaticCollector,
        builder: NextScriptBuilder,
        render: _Render,
        bundles: str,
    ) -> str:
        payload = collector.js_context_payload(reserved=render.reserved)
        dev = bool(settings.DEBUG)
        listed = render.parts.listed
        enabled = listed | _DEV if dev else listed
        chunks, chunks_encoded = self._chunks_entry(render.request, enabled)
        reserved = self._reserved_payload(render, dev=dev)
        js_context = {**payload.values, **reserved, CHUNKS_PAYLOAD_KEY: chunks}
        encoded = {**payload.encoded, CHUNKS_PAYLOAD_KEY: chunks_encoded}
        csrf = reserved.get(CSRF_PAYLOAD_KEY)
        if csrf is not None:
            encoded[CSRF_PAYLOAD_KEY] = self._csrf_entry(csrf)
        nonce = render.nonce()
        init_payload = builder.init_script(
            js_context,
            key_serializers=payload.serializers,
            encoded=encoded,
            shared=render.parts.shared,
            nonce=nonce,
        )
        if after := render.parts.after:
            init_payload += "".join(write(nonce) for write in after)
        tag = builder.script_tag(render.runtime_url, nonce=nonce)
        next_scripts = f"{tag}\n{bundles}{init_payload}\n"
        return next_scripts + user_tags if user_tags else next_scripts

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
        attrs = render.parts.stylesheet
        if not attrs or (
            default_kinds.load(asset.kind, inline=asset.inline is not None)
            != _STYLESHEET
        ):
            return rendered
        return _marked(rendered, attrs)

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


class _Filled:
    """The rendered tags of one injection, keyed by slot name, with the extras."""

    __slots__ = ("head", "last", "tags")

    def __init__(self) -> None:
        """Hold nothing rendered yet."""
        self.tags: dict[str, str] = {}
        self.head: str | None = None
        self.last: str | None = None


_TOKENS: list[tuple[int, str, tuple[PlaceholderSlot, ...]]] = []


def _tokens() -> tuple[str, tuple[PlaceholderSlot, ...]]:
    """Return the prefix every registered token opens with and the slots, longest first.

    Held until the registry moves, so a page is searched for that prefix alone.
    """
    version = default_placeholders.version
    if _TOKENS and _TOKENS[0][0] == version:
        return _TOKENS[0][1], _TOKENS[0][2]
    slots = tuple(sorted(default_placeholders, key=_length, reverse=True))
    prefix = _shared([slot.token for slot in slots])
    _TOKENS[:] = [(version, prefix, slots)]
    return prefix, slots


def _shared(tokens: Sequence[str]) -> str:
    """Return the longest text every one of `tokens` opens with."""
    first, last = min(tokens, default=""), max(tokens, default="")
    pairs = enumerate(zip(first, last, strict=False))
    return first[: next((at for at, (a, b) in pairs if a != b), len(first))]


def _length(slot: PlaceholderSlot) -> int:
    """Return the length of the token of `slot`."""
    return len(slot.token)


def _spots(html: str) -> list[tuple[int, PlaceholderSlot]]:
    """Return where each placeholder token of `html` opens, in document order.

    One walk over the prefix the tokens share finds them all in a single pass.
    """
    prefix, slots = _tokens()
    found: list[tuple[int, PlaceholderSlot]] = []
    if not prefix:
        for slot in slots:
            at = html.find(slot.token)
            while at >= 0:
                found.append((at, slot))
                at = html.find(slot.token, at + len(slot.token))
        found.sort(key=_start)
        return found
    at = html.find(prefix)
    while at >= 0:
        step = len(prefix)
        for slot in slots:
            if html.startswith(slot.token, at):
                found.append((at, slot))
                step = len(slot.token)
                break
        at = html.find(prefix, at + step)
    return found


def _start(spot: tuple[int, PlaceholderSlot]) -> int:
    """Return where one found token opens."""
    return spot[0]


def _present(spots: Sequence[tuple[int, PlaceholderSlot]]) -> list[PlaceholderSlot]:
    """Return the slots `spots` holds, in registration order."""
    held = {slot.name for _, slot in spots}
    return [slot for slot in default_placeholders if slot.name in held]


def _spliced(
    html: str,
    spots: Sequence[tuple[int, PlaceholderSlot]],
    filled: _Filled,
    hint: str | None,
) -> str:
    """Return `html` with each token replaced and the head extras before `</head>`.

    The last scripts token takes the split mark, and the page is copied once.
    """
    edits = [(at, len(slot.token), filled.tags[slot.name]) for at, slot in spots]
    if filled.last is not None:
        index = max(
            index
            for index, (_, slot) in enumerate(spots)
            if slot.name == _RUNTIME_SLOT_NAME
        )
        at, size, _ = edits[index]
        edits[index] = (at, size, filled.last)
    extra = "".join(f"{text}\n" for text in (filled.head, hint) if text)
    close = html.find(HEAD_CLOSE) if extra else -1
    if close >= 0:
        edits.append((close, 0, extra))
        edits.sort(key=_edit_start)
    if not edits:
        return html
    pieces: list[str] = []
    position = 0
    for at, size, text in edits:
        pieces.append(html[position:at])
        pieces.append(text)
        position = at + size
    pieces.append(html[position:])
    return "".join(pieces)


def _edit_start(edit: tuple[int, int, str]) -> int:
    """Return where one edit of the page opens."""
    return edit[0]


def _marked(tag: str, attrs: str) -> str:
    """Return `tag` with the provider attributes `attrs` after its first element name.

    The client router disables a marked stylesheet the next page does not list.
    """
    opening = _OPENING.search(tag)
    if opening is None:
        return tag
    at = opening.end()
    return f"{tag[:at]}{attrs}{tag[at:]}"


def _eager_urls(collector: StaticCollector, names: Sequence[str]) -> frozenset[str]:
    """Return the URLs of the declared bundles a provider loads as soon as it can."""
    resolve = collector.bundle_url
    if resolve is None or not names:
        return frozenset()
    return frozenset(url for name in names if (url := resolve(name)) is not None)


def _note_dropped(collector: StaticCollector, page_path: Path | None) -> None:
    """Log at a bounded rate that the bundles a render needs reach no tag."""
    urls = ", ".join(str(url) for url in collector.notes(BUNDLE_NOTE))
    _dropped.emit(logging.DEBUG, page_path, _DROPPED, (page_path, urls))


def _split_mark() -> str:
    """Return a mark no page text can hold, fresh for each render."""
    return f"\x00next-split-{secrets.token_hex(_MARK_BYTES)}\x00"


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
