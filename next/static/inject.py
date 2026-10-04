"""Render collected assets into the placeholder tokens of a finished page.

The injector reads the backend, the URL rewrite, and the script builder from the
static manager, and holds every step that renders a collector into markup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Protocol

from django.conf import settings

from next.ports import page_scripts_slot

from .assets import default_kinds
from .collector import HEAD_CLOSE, default_placeholders
from .nonce import resolve_nonce
from .runtime import (
    CHUNK_STATIC_PATHS,
    CHUNKS_PAYLOAD_KEY,
    CSRF_PAYLOAD_KEY,
    DEV_CHUNK_KEY,
    DEV_PAYLOAD_KEY,
    RESERVED_PAYLOAD_KEYS,
    ScriptInjectionPolicy,
    csrf_payload_for,
    nonce_attr,
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

# The storage URLs of the listed chunks, the URLs a page names, the serializer type
# and the encoded entry.
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

    __slots__ = ("head", "payload", "request", "runtime_url")

    def __init__(self, request: HttpRequest | None, runtime_url: str | None) -> None:
        """Hold the request and the runtime URL, with no third-party scripts yet."""
        self.request = request
        self.runtime_url = runtime_url
        self.head, self.payload = _NO_SCRIPTS

    def nonce(self) -> str | None:
        """Return the request nonce, resolved by the first tag that carries it.

        A minted nonce makes the response private, so a render that writes no tag
        leaves the page shareable.
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

        Every URL passes through `asset_url` first, so a per-request rewrite applies to
        every renderer and both signals. A new asset kind needs no change here.
        """
        sender = self._provider
        collector_finalized.send(sender=collector, page_path=page_path, request=request)
        html_before = html
        replaced: tuple[str, ...] | None = None
        if html_injected.has_listeners(sender):
            replaced = tuple(
                slot.name for slot in default_placeholders if slot.token in html
            )
        # The runtime loads only from the scripts placeholder, so a page without one
        # gets no preload hint for it.
        runtime_slot = default_placeholders.get(_RUNTIME_SLOT_NAME)
        hints_runtime = runtime_slot is not None and runtime_slot.token in html
        backend = sender.default_backend
        builder = sender.script_builder()
        # Resolved once per render, so the script tag and the preload hint share one
        # URL and the backend hook runs once.
        runtime_url = (
            sender.asset_url(builder.url, request=request)
            if builder.policy is ScriptInjectionPolicy.AUTO
            else None
        )
        render = _Render(request, runtime_url)
        scripts = page_scripts_slot.peek()
        if scripts is not None:
            render.head, render.payload = scripts.render(
                collector, page_path=page_path, request=request, nonce=render.nonce
            )
        for slot in default_placeholders:
            rendered = self._render_slot(slot, collector, backend, builder, render)
            if slot.name == _HEAD_SLOT_NAME and rendered and slot.token not in html:
                html = html.replace(HEAD_CLOSE, f"{rendered}\n{HEAD_CLOSE}", 1)
            else:
                html = html.replace(slot.token, rendered)
        if runtime_url is not None and hints_runtime:
            html = self._inject_preload_hint(html, builder, runtime_url, render.nonce())
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

    def _render_slot(
        self,
        slot: PlaceholderSlot,
        collector: StaticCollector,
        backend: StaticBackend,
        builder: NextScriptBuilder,
        render: _Render,
    ) -> str:
        user_tags = self._render_tags(
            collector.assets_in_slot(slot.name), backend, render
        )
        if slot.name == _HEAD_SLOT_NAME and render.head:
            return f"{render.head}\n{user_tags}" if user_tags else render.head
        if slot.name == _RUNTIME_SLOT_NAME and render.runtime_url is not None:
            return self._wrap_with_runtime(user_tags, collector, builder, render)
        return user_tags

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

        Every chunk is listed because a later patch may need any of them, except the
        dev chunk, which only `DEBUG` lists. A chunk the storage lacks is left out.
        Without a per-request rewrite the page URLs follow from the storage URLs, so
        the backend is asked only when those move.
        """
        provider = self._provider
        stored = tuple(
            (name, url)
            for name in CHUNK_STATIC_PATHS
            if (dev or name != DEV_CHUNK_KEY) and (url := provider.chunk_url(name))
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
        next_scripts = f"{tag}\n{init_payload}\n"
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


__all__ = ["InjectionProvider", "PlaceholderInjector"]
