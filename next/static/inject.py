"""Render collected assets into the placeholder tokens of a finished page.

The injector reads the backend, the URL rewrite, and the script builder from the
static manager, and owns everything that turns a collector into markup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from django.conf import settings

from .assets import default_kinds
from .collector import HEAD_CLOSE, default_placeholders
from .scripts import (
    CSRF_PAYLOAD_KEY,
    DEV_PAYLOAD_KEY,
    RESERVED_PAYLOAD_KEYS,
    ScriptInjectionPolicy,
    csrf_payload_for,
)
from .signals import collector_finalized, html_injected


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from django.http import HttpRequest

    from .assets import StaticAsset
    from .backends import StaticBackend
    from .collector import PlaceholderSlot, StaticCollector
    from .scripts import NextScriptBuilder


_RUNTIME_SLOT_NAME = "scripts"


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


class PlaceholderInjector:
    """Replace placeholder tokens with the tags a collector accumulated."""

    def __init__(self, provider: InjectionProvider) -> None:
        """Bind the pipeline the rendered tags and URLs are read from."""
        self._provider = provider

    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path | None = None,
        request: HttpRequest | None = None,
    ) -> str:
        """Replace every registered placeholder token with rendered tags.

        Every URL passes `asset_url` first, so a per-request rewrite reaches every
        renderer and both signals alike. A new kind needs no change here.
        """
        sender = self._provider
        collector_finalized.send(sender=collector, page_path=page_path, request=request)
        html_before = html
        replaced: tuple[str, ...] | None = None
        if html_injected.has_listeners(sender):
            replaced = tuple(
                slot.name for slot in default_placeholders if slot.token in html
            )
        backend = sender.default_backend
        builder = sender.script_builder()
        # Settled once for the render, so the script tag and the preload hint
        # cannot disagree and a backend doing real work in the hook pays for one.
        runtime_url = (
            sender.asset_url(builder.url, request=request)
            if builder.policy is ScriptInjectionPolicy.AUTO
            else None
        )
        for slot in default_placeholders:
            rendered = self._render_slot(
                slot,
                collector,
                backend,
                builder,
                request=request,
                runtime_url=runtime_url,
            )
            html = html.replace(slot.token, rendered)
        if runtime_url is not None:
            html = self._inject_preload_hint(html, builder, runtime_url)
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
        *,
        request: HttpRequest | None,
        runtime_url: str | None,
    ) -> str:
        user_tags = self._render_tags(
            collector.assets_in_slot(slot.name), backend, request=request
        )
        if slot.name == _RUNTIME_SLOT_NAME and runtime_url is not None:
            return self._wrap_with_runtime(
                user_tags, collector, builder, runtime_url, request=request
            )
        return user_tags

    def _reserved_payload(self, request: HttpRequest | None) -> dict[str, Any]:
        """Return the framework-owned init-payload entries for this render."""
        payload: dict[str, Any] = {}
        csrf = csrf_payload_for(request)
        if csrf is not None:
            payload[CSRF_PAYLOAD_KEY] = csrf
        if settings.DEBUG:
            payload[DEV_PAYLOAD_KEY] = True
        return payload

    def _wrap_with_runtime(
        self,
        user_tags: str,
        collector: StaticCollector,
        builder: NextScriptBuilder,
        runtime_url: str,
        *,
        request: HttpRequest | None,
    ) -> str:
        payload = collector.js_context_payload(reserved=RESERVED_PAYLOAD_KEYS)
        js_context = payload.values
        reserved = self._reserved_payload(request)
        if reserved:
            js_context = {**js_context, **reserved}
        init_payload = builder.init_script(
            js_context, key_serializers=payload.serializers, encoded=payload.encoded
        )
        next_scripts = f"{builder.script_tag(runtime_url)}\n{init_payload}\n"
        return next_scripts + user_tags if user_tags else next_scripts

    def _inject_preload_hint(
        self, html: str, builder: NextScriptBuilder, runtime_url: str
    ) -> str:
        replacement = f"{builder.preload_link(runtime_url)}\n{HEAD_CLOSE}"
        return html.replace(HEAD_CLOSE, replacement, 1)

    def _render_tags(
        self,
        assets: Sequence[StaticAsset],
        backend: StaticBackend,
        *,
        request: HttpRequest | None,
    ) -> str:
        return "\n".join(self._render_one(asset, backend, request) for asset in assets)

    def _render_one(
        self, asset: StaticAsset, backend: StaticBackend, request: HttpRequest | None
    ) -> str:
        if asset.inline is not None:
            tag = default_kinds.inline_tag(asset.kind)
            if tag is None:
                return asset.inline
            return f"<{tag}>{asset.inline}</{tag}>"
        renderer_name = default_kinds.renderer(asset.kind)
        renderer = getattr(backend, renderer_name)
        url = self._provider.asset_url(asset.url, request=request)
        return cast("str", renderer(url, request=request))


__all__ = ["InjectionProvider", "PlaceholderInjector"]
