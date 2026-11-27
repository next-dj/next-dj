"""The router state one page render shares with the injector, and its script provider.

The injector knows no router, so the state reaches the page through `next.ports`.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

from next.caches import SETTINGS, LruCache
from next.conf.router import router_enabled
from next.ports import ScriptShare
from next.static.runtime import (
    BOUNDARIES_CHUNK_KEY,
    INTENT_CHUNK_KEY,
    ROUTER_CHUNK_KEY,
    ROUTER_PAYLOAD_KEY,
    nonce_attr,
    payload_entry,
)
from next.wire import ASSET_ATTR


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from next.ports import ScriptRender
    from next.static.collector import StaticCollector


ASSETS_MARK: Final = "nx:a"
"""The kind of the enclosing comments around each placeholder the injector fills."""

LAYOUTS_ATTR: Final = "data-next-layouts"
"""The JSON block of a routed document, `[id, digest, response id]` per layout."""

PAGE_ASSET_ATTR: Final = f' {ASSET_ATTR}="page"'
"""The attribute a routed render writes on the stylesheets the client router retires."""

PRIORITY: Final = 400
"""The rank of the router among the page script providers, after the stream flag."""

_ENTRIES_HELD: Final = 64
_BOUNDARIES_FIELD: Final = f',"{BOUNDARIES_CHUNK_KEY}":'
_INTENT_FIELD: Final = f',"{INTENT_CHUNK_KEY}":'
_NO_SNAPSHOT_FIELD: Final = ',"snapshot":false'
_EAGER: Final = (ROUTER_CHUNK_KEY,)
_LAYOUTS_BLOCK: Final = (
    f'<script type="application/json" {LAYOUTS_ATTR}{{nonce_attr}}>{{mark}}</script>'
)


def speculation_tag(rules: str, nonce: str | None) -> str:
    """Return the `<script type="speculationrules">` holding the encoded `rules`."""
    return f'<script type="speculationrules"{nonce_attr(nonce)}>{rules}</script>'


class RouterRender:
    """What one page render under the client router shares with the injector.

    The injector encloses its output and runs `finish` before it notes a split offset.
    """

    __slots__ = (
        "boundaries",
        "entry",
        "finish",
        "intent",
        "mark",
        "page",
        "response_id",
        "snapshot",
        "speculation",
    )

    def __init__(
        self,
        response_id: str,
        entry: str,
        finish: Callable[[str], str] | None = None,
        *,
        snapshot: bool = True,
        speculation: str = "",
    ) -> None:
        """Hold the response id, the `$router` entry, the last pass and the head extras.

        The head carries `speculation` unless it is empty.
        """
        self.response_id = response_id
        self.entry = entry
        self.finish = finish
        self.snapshot = snapshot
        self.speculation = speculation
        self.mark = f'"nx:layouts {response_id}"'
        self.page: ScriptRender | None = None
        self.boundaries: str | None = None
        self.intent: str | None = None

    @property
    def collector(self) -> StaticCollector | None:
        """Return the collector of the injected page, `None` before the injection."""
        return None if self.page is None else self.page.collector

    @property
    def payload(self) -> Mapping[str, object]:
        """Return the request payload entries the providers gave the injected page."""
        return {} if self.page is None else self.page.entries

    def encoded(self) -> str:
        """Return the `$router` entry with the bundle URLs and the snapshot refusal.

        A document cannot read its own headers, so the payload carries the refusal.
        """
        tail = ""
        if self.boundaries is not None:
            tail = f"{_BOUNDARIES_FIELD}{json.dumps(self.boundaries)}"
        if self.intent is not None:
            tail += f"{_INTENT_FIELD}{json.dumps(self.intent)}"
        if not self.snapshot:
            tail += _NO_SNAPSHOT_FIELD
        return f"{self.entry[:-1]}{tail}}}" if tail else self.entry

    def enclosed(self, html: str) -> str:
        """Return `html` between the enclosing comments of an injected placeholder."""
        ident = self.response_id
        return f"<!--{ASSETS_MARK} {ident}-->{html}<!--/{ASSETS_MARK} {ident}-->"

    def layouts(self, nonce: str | None) -> str:
        """Return the layouts block after the init script, which `finish` fills."""
        return _LAYOUTS_BLOCK.format(mark=self.mark, nonce_attr=nonce_attr(nonce))

    def share(self, render: ScriptRender) -> ScriptShare:
        """Return what the router adds to `render`, keeping the render for its reply.

        The boundaries bundle serves a streamed navigation, the intent one loads idle.
        """
        self.page = render
        self.boundaries = render.bundle_url(BOUNDARIES_CHUNK_KEY)
        self.intent = render.bundle_url(INTENT_CHUNK_KEY)
        speculation = self.speculation
        return ScriptShare(
            head=speculation_tag(speculation, render.nonce()) if speculation else "",
            shared=_entry(self),
            after=self.layouts,
            eager=_EAGER,
            enclose=self.enclosed,
            finish=self.finish,
            stylesheet=PAGE_ASSET_ATTR,
        )


router_render: ContextVar[RouterRender | None] = ContextVar(
    "next_router_render", default=None
)
"""The router state of the page under render, `None` while the router is off."""


_entries: Final = LruCache[tuple[str, str | None, str | None, bool], str](
    _ENTRIES_HELD, depends=(SETTINGS,)
)


def _entry(routed: RouterRender) -> str:
    """Return the encoded `$router` entry, built once per entry, bundles and refusal."""
    key = (routed.entry, routed.boundaries, routed.intent, routed.snapshot)
    held = _entries.get(key)
    if held is None:
        held = _entries[key] = payload_entry(ROUTER_PAYLOAD_KEY, routed.encoded())
    return held


class RouterScripts:
    """The router's part in every page head and init payload, while it is on."""

    name: Final = "router"
    priority: Final = PRIORITY
    keys: Final = frozenset({ROUTER_PAYLOAD_KEY})

    def applies(self) -> bool:
        """Return whether the client router is on."""
        return router_enabled()

    def share(self, render: ScriptRender, /) -> ScriptShare | None:
        """Return what the router state of the page adds, `None` off a routed page."""
        routed = router_render.get()
        return None if routed is None else routed.share(render)


__all__ = [
    "ASSETS_MARK",
    "LAYOUTS_ATTR",
    "PAGE_ASSET_ATTR",
    "RouterRender",
    "RouterScripts",
    "router_render",
    "speculation_tag",
]
