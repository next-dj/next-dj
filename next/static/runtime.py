"""Build the `next.min.js` preload, script and init tags, and name its lazy chunks."""

from __future__ import annotations

import enum
import functools
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass
from html import escape
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final

from next.caches import DEFAULT_CACHE_SIZE
from next.csrf import csrf_payload
from next.diagnostics import FailureLog
from next.ports import realtime_negotiation_slot

from .serializers import resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from django.http import HttpRequest

    from .collector import StaticCollector
    from .serializers import JsContextSerializer


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)

NEXT_JS_STATIC_PATH: Final = "next/next.min.js"

SCRIPTS_CHUNK_STATIC_PATH: Final = "next/next.scripts.min.js"
"""The optional chunk carrying consent and third-party scripts."""

DEV_CHUNK_STATIC_PATH: Final = "next/next.dev.min.js"
"""The diagnostics chunk the runtime fetches only when the payload carries `$dev`."""

STREAM_CHUNK_STATIC_PATH: Final = "next/next.stream.min.js"
"""The transport-neutral stream state machine, fetched by stream containers."""

WS_CHUNK_STATIC_PATH: Final = "next/next.ws.min.js"
"""The optional WebSocket adapter, fetched only when WS is enabled."""

CSRF_CHUNK_STATIC_PATH: Final = "next/next.csrf.min.js"
"""The deferred CSRF token loader, fetched on the first post from a page without one."""

POLL_CHUNK_STATIC_PATH: Final = "next/next.poll.min.js"
"""The zone poller, fetched once a page marks a zone to poll."""

DEFER_CHUNK_STATIC_PATH: Final = "next/next.defer.min.js"
"""The client-trip loader, declared by a page holding a token or a retry control."""

RELOAD_CHUNK_STATIC_PATH: Final = "next/next.reload.min.js"
"""The zone skeletons, declared by a page holding a zone that reloads to its loading."""

MORE_CHUNK_STATIC_PATH: Final = "next/next.more.min.js"
"""The `{% more %}` URL sync and pruning, declared by a page holding such a link."""

READER_CHUNK_STATIC_PATH: Final = "next/next.reader.min.js"
"""The reader of streamed documents and partials, declared by a page that streams."""

ROUTER_CHUNK_STATIC_PATH: Final = "next/next.router.min.js"
"""The client router, declared by every document while `ROUTER["ENABLED"]` holds."""

HISTORY_CHUNK_STATIC_PATH: Final = "next/next.history.min.js"
"""The snapshots, scroll restore and active links the router loads once idle."""

INTENT_CHUNK_STATIC_PATH: Final = "next/next.intent.min.js"
"""The intent prefetch and the client cache the router loads once idle."""

DEV_CHUNK_KEY: Final = "dev"
"""The `$chunks` key of the diagnostics chunk, listed only under `DEBUG`."""

WS_CHUNK_KEY: Final = "ws"
"""The `$chunks` key of the WebSocket adapter, listed only while `ws` is active."""

DEFER_CHUNK_KEY: Final = "defer"
RELOAD_CHUNK_KEY: Final = "reload"
MORE_CHUNK_KEY: Final = "more"
READER_CHUNK_KEY: Final = "reader"
ROUTER_CHUNK_KEY: Final = "router"
HISTORY_CHUNK_KEY: Final = "history"
INTENT_CHUNK_KEY: Final = "intent"

DECLARED_CHUNK_KEYS: Final = frozenset(
    {
        DEFER_CHUNK_KEY,
        RELOAD_CHUNK_KEY,
        MORE_CHUNK_KEY,
        READER_CHUNK_KEY,
        ROUTER_CHUNK_KEY,
        HISTORY_CHUNK_KEY,
        INTENT_CHUNK_KEY,
    }
)
"""The bundles a page names by script tag or in `$router`, never in `$chunks`."""

DEFAULT_TRANSPORTS: Final = ("sse",)
"""The transports the runtime assumes when the payload carries no `$realtime`."""

CHUNK_STATIC_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "scripts": SCRIPTS_CHUNK_STATIC_PATH,
        "stream": STREAM_CHUNK_STATIC_PATH,
        WS_CHUNK_KEY: WS_CHUNK_STATIC_PATH,
        "csrf": CSRF_CHUNK_STATIC_PATH,
        "poll": POLL_CHUNK_STATIC_PATH,
        DEV_CHUNK_KEY: DEV_CHUNK_STATIC_PATH,
        DEFER_CHUNK_KEY: DEFER_CHUNK_STATIC_PATH,
        RELOAD_CHUNK_KEY: RELOAD_CHUNK_STATIC_PATH,
        MORE_CHUNK_KEY: MORE_CHUNK_STATIC_PATH,
        READER_CHUNK_KEY: READER_CHUNK_STATIC_PATH,
        ROUTER_CHUNK_KEY: ROUTER_CHUNK_STATIC_PATH,
        HISTORY_CHUNK_KEY: HISTORY_CHUNK_STATIC_PATH,
        INTENT_CHUNK_KEY: INTENT_CHUNK_STATIC_PATH,
    }
)
"""Every lazy bundle the package ships, by the name the runtime knows it under."""

LISTED_CHUNK_KEYS: Final = tuple(
    name for name in CHUNK_STATIC_PATHS if name not in DECLARED_CHUNK_KEYS
)
"""The chunks `$chunks` may list, every shipped one but the declared bundles."""

BUNDLE_NOTE: Final = "next:bundle"
"""The collector note holding the URL of each declared bundle a render needs."""

URGENT_NOTE: Final = "next:urgent"
"""The collector note a render holding a `load` boundary sets, hurrying the bundles."""

BUNDLE_NOTES: Final = frozenset({BUNDLE_NOTE, URGENT_NOTE})

CSRF_PAYLOAD_KEY: Final = "$csrf"

# Sent only under `DEBUG`, so a production payload never requests the dev chunk.
DEV_PAYLOAD_KEY: Final = "$dev"

CHUNKS_PAYLOAD_KEY: Final = "$chunks"
REALTIME_PAYLOAD_KEY: Final = "$realtime"
SCRIPTS_PAYLOAD_KEY: Final = "$scripts"
CONSENT_PAYLOAD_KEY: Final = "$consent"
STREAM_PAYLOAD_KEY: Final = "$stream"
"""The payload flag telling the runtime that a partial `GET` may answer as a stream."""

ROUTER_PAYLOAD_KEY: Final = "$router"
"""The settings the client router reads, present only while the router is on."""

_READER_FIELD: Final = f',"{READER_CHUNK_KEY}":'
_HISTORY_FIELD: Final = f',"{HISTORY_CHUNK_KEY}":'
_INTENT_FIELD: Final = f',"{INTENT_CHUNK_KEY}":'
_NO_SNAPSHOT_FIELD: Final = ',"snapshot":false'

LAYOUTS_PAYLOAD_KEY: Final = "$layouts"
"""The `[id, digest, seal]` of each layout of the document, outer first."""

ASSETS_MARK: Final = "nx:a"
"""The kind of the sealed comments around each placeholder the injector fills."""

partial_streams: ContextVar[bool] = ContextVar("next_partial_streams", default=False)
"""Whether the page under render may receive streamed partial responses."""

CHUNK_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {SCRIPTS_PAYLOAD_KEY, CONSENT_PAYLOAD_KEY}
)
"""The payload keys that make the runtime fetch the scripts chunk."""

RESERVED_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        CSRF_PAYLOAD_KEY,
        DEV_PAYLOAD_KEY,
        CHUNKS_PAYLOAD_KEY,
        REALTIME_PAYLOAD_KEY,
        STREAM_PAYLOAD_KEY,
        ROUTER_PAYLOAD_KEY,
        LAYOUTS_PAYLOAD_KEY,
        *CHUNK_PAYLOAD_KEYS,
    }
)

SCRIPT_ESCAPES: Final[dict[int, str]] = {
    ord("<"): "\\u003C",
    ord(">"): "\\u003E",
    ord("&"): "\\u0026",
    ord("\u2028"): "\\u2028",
    ord("\u2029"): "\\u2029",
}
"""Escapes for JSON inside a `<script>`, mirroring Django's `json_script`.

These code points occur only inside JSON strings, so the escaped payload decodes to
the same value.
"""

_ESCAPED: Final = re.compile(f"[{re.escape(''.join(map(chr, SCRIPT_ESCAPES)))}]")
"""Matches a character `SCRIPT_ESCAPES` rewrites, a cheaper test than `translate`."""


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def _encoded_key(key: str) -> str:
    """Return a payload key encoded as JSON, memoised as `json.dumps` is costly."""
    return json.dumps(key, separators=(",", ":"))


def nonce_attr(nonce: str | None) -> str:
    """Return the ` nonce="..."` attribute a tag carries, empty without a nonce."""
    return f' nonce="{escape(nonce)}"' if nonce else ""


PAGE_ASSET_ATTR: Final = ' data-next-asset="page"'
"""The attribute a routed render writes on the stylesheets the client router retires."""


PROGRESS_STYLE: Final = (
    'html[data-next-navigating]::before{content:"";position:fixed;inset:0 0 auto;'
    "z-index:2147483647;height:var(--next-progress-height,3px);"
    "background:var(--next-progress-color,LinkText);transform-origin:0 0;"
    "pointer-events:none;animation:next-progress var(--next-progress-duration,8s) "
    "cubic-bezier(.1,.7,.2,1) both}"
    "@keyframes next-progress{from{transform:scaleX(.1)}to{transform:scaleX(.95)}}"
    "@media (prefers-reduced-motion:reduce){html[data-next-navigating]::before"
    "{animation:none;transform:none}}"
)
"""The default progress bar, shown while `<html>` holds the navigating mark."""


def progress_tag(nonce: str | None) -> str:
    """Return the `<style>` holding the default progress bar, with the request nonce."""
    return f"<style{nonce_attr(nonce)}>{PROGRESS_STYLE}</style>"


def speculation_tag(rules: str, nonce: str | None) -> str:
    """Return the `<script type="speculationrules">` holding the encoded `rules`."""
    return f'<script type="speculationrules"{nonce_attr(nonce)}>{rules}</script>'


def csrf_payload_for(request: HttpRequest | None) -> dict[str, str] | None:
    """Return the `$csrf` payload, or None when the request cannot mint a token.

    A request whose `META` is not a dict, such as a test double, yields no payload.
    """
    if request is None or not isinstance(getattr(request, "META", None), dict):
        return None
    return csrf_payload(request)


class RouterRender:
    """What one page render under the client router shares with the injector.

    The injector seals what it writes, records the collector and the scripts payload,
    and hands the finished HTML to `finish` before it notes any split offset.
    """

    __slots__ = (
        "collector",
        "entry",
        "finish",
        "history",
        "intent",
        "mark",
        "payload",
        "progress",
        "reader",
        "seal",
        "snapshot",
        "speculation",
    )

    def __init__(
        self,
        seal: str,
        entry: str,
        finish: Callable[[str], str] | None = None,
        *,
        snapshot: bool = True,
        speculation: str = "",
        progress: bool = False,
    ) -> None:
        """Hold the seal, the `$router` entry, the final pass and the head extras.

        The head carries `speculation` unless empty and the progress bar if `progress`.
        """
        self.seal = seal
        self.entry = entry
        self.finish = finish
        self.snapshot = snapshot
        self.speculation = speculation
        self.progress = progress
        self.mark = f'"nx:layouts {seal}"'
        self.collector: StaticCollector | None = None
        self.payload: Mapping[str, object] = {}
        self.reader: str | None = None
        self.history: str | None = None
        self.intent: str | None = None

    def encoded(self) -> str:
        """Return the `$router` entry with the bundle URLs and the snapshot refusal.

        A document cannot read its own headers, so the payload carries the refusal.
        """
        tail = ""
        if self.reader is not None:
            tail = f"{_READER_FIELD}{json.dumps(self.reader)}"
        if self.history is not None:
            tail += f"{_HISTORY_FIELD}{json.dumps(self.history)}"
        if self.intent is not None:
            tail += f"{_INTENT_FIELD}{json.dumps(self.intent)}"
        if not self.snapshot:
            tail += _NO_SNAPSHOT_FIELD
        return f"{self.entry[:-1]}{tail}}}" if tail else self.entry

    def sealed(self, html: str) -> str:
        """Return `html` between the sealed comments of an injected placeholder."""
        return (
            f"<!--{ASSETS_MARK} {self.seal}-->{html}<!--/{ASSETS_MARK} {self.seal}-->"
        )


router_render: ContextVar[RouterRender | None] = ContextVar(
    "next_router_render", default=None
)
"""The router state of the page under render, `None` while the router is off."""

html_finish: ContextVar[Callable[[str], str] | None] = ContextVar(
    "next_html_finish", default=None
)
"""The final pass the view runs over the unrouted HTML it sends, before any split."""


@dataclass(frozen=True, slots=True)
class RealtimeOffer:
    """The `$realtime` payload entry and whether the `ws` chunk is listed.

    An empty entry leaves the stock configuration to the runtime defaults.
    """

    entry: str
    websocket: bool


def realtime_offer() -> RealtimeOffer:
    """Return the realtime negotiation every page carries, memoised per config.

    The memo is keyed on the content types, so a reset partial backend never lingers.
    """
    return _offer(realtime_negotiation_slot.get().content_types())


@functools.lru_cache(maxsize=1)
def _offer(types: tuple[str, str]) -> RealtimeOffer:
    """Build the offer for the enabled transports and one pair of content types."""
    transports = realtime_negotiation_slot.get().transports()
    content_type, stock = types
    if transports == DEFAULT_TRANSPORTS and content_type == stock:
        return RealtimeOffer("", websocket=False)
    payload = json.dumps(
        {"protocol": 2, "transports": transports, "content_type": content_type},
        separators=(",", ":"),
    )
    return RealtimeOffer(
        f"{_encoded_key(REALTIME_PAYLOAD_KEY)}:{payload}",
        websocket=WS_CHUNK_KEY in transports,
    )


def forget_realtime_offer(**kwargs: object) -> None:
    """Drop the memoised offer when the transports change."""
    _offer.cache_clear()


TAG_FIELDS: Final = ("url", "nonce_attr")
"""The fields a tag template naming an asset is formatted with."""

INIT_FIELDS: Final = ("payload", "nonce_attr")
"""The fields the init script template is formatted with."""

CHUNK_FIELDS: Final = INIT_FIELDS
"""The fields the chunk script template of a streamed page is formatted with."""

TEMPLATE_ERRORS: Final = (KeyError, IndexError, ValueError, AttributeError)
"""The exceptions `str.format` raises for an unbalanced brace or an unknown field."""


def dry_run_template(template: str, fields: Iterable[str]) -> None:
    """Format `template` with a blank value per field, raising what a render would."""
    template.format(**dict.fromkeys(fields, ""))


_SCRIPT_TEXT: Final = re.compile(
    r"<script\b[^>]*>(?P<text>.*?)</script\s*>", re.IGNORECASE | re.DOTALL
)
_PROBE: Final = "\ue000"
_SCRIPT_START: Final = "<script"
_SCRIPT_OPEN: Final = re.compile(rf"{_SCRIPT_START}\b", re.IGNORECASE)


def payload_in_script(template: str) -> bool:
    """Whether every `{payload}` of a chunk template lands in the text of a `<script>`.

    The payload is escaped for script text only, which an attribute does not hold.
    """
    try:
        rendered = template.format(payload=_PROBE, nonce_attr="")
    except TEMPLATE_ERRORS:
        return True
    texts = _SCRIPT_TEXT.finditer(rendered)
    inside = sum(found["text"].count(_PROBE) for found in texts)
    return inside == rendered.count(_PROBE)


def usable_template(
    template: str, default: str, fields: Iterable[str], where: str
) -> str:
    """Return `template` when it formats with `fields`, else `default`.

    The error is raised under `DEBUG` and otherwise logged at the `FailureLog` rate.
    """
    if template == default:
        return default
    guard = _failures.guard(
        where,
        "%s %r does not format with the fields %s, so the default tag renders instead. "
        "Double every literal brace as {{ or }} and name no other field.",
        where,
        template,
        ", ".join(f"{{{name}}}" for name in fields),
        catch=TEMPLATE_ERRORS,
    )
    with guard:
        dry_run_template(template, fields)
    return default if guard.failed else template


CHUNK_TEMPLATE: Final = "NEXT_JS_OPTIONS['chunk_template']"


def _script_chunk(template: str, default: str) -> str:
    """Return `template`, or `default` when it puts the payload outside script text."""
    if payload_in_script(template):
        return template
    _failures.warn(
        CHUNK_TEMPLATE,
        "%s %r puts {payload} outside the text of a <script> element, where its "
        "escaping does not hold, so the default chunk renders instead.",
        CHUNK_TEMPLATE,
        template,
    )
    return default


class BundleLoad(enum.Enum):
    """How the tag of a declared bundle loads it, each value the attributes it adds."""

    LAZY = ' async fetchpriority="low"'
    EAGER = " async"
    DEFERRED = " defer"


def bundle_load(*, reader: bool, streams: bool, urgent: bool) -> BundleLoad:
    """Return how a declared bundle loads, `reader` naming the reader of streams.

    `defer` waits for the whole document, which on a stream arrives with its last chunk.
    """
    if streams and (reader or urgent):
        return BundleLoad.EAGER
    if urgent and not reader:
        return BundleLoad.DEFERRED
    return BundleLoad.LAZY


class ScriptInjectionPolicy(enum.Enum):
    """Controls whether `next.min.js` is automatically injected."""

    AUTO = "auto"
    DISABLED = "disabled"
    MANUAL = "manual"


class NextScriptBuilder:
    """Builds the preload hint, script tag, and init script for `window.Next`.

    Tag templates take `{url}`, init and chunk ones `{payload}`, all `{nonce_attr}`.
    """

    DEFAULT_PRELOAD: ClassVar[str] = (
        '<link rel="preload" as="script" href="{url}"{nonce_attr}>'
    )
    DEFAULT_SCRIPT_TAG: ClassVar[str] = '<script src="{url}"{nonce_attr}></script>'
    DEFAULT_INIT: ClassVar[str] = "<script{nonce_attr}>Next._init({payload});</script>"
    DEFAULT_CHUNK: ClassVar[str] = (
        "<script{nonce_attr}>(self.$nx||=[]).push({payload})</script>"
    )

    def __init__(
        self,
        next_js_url: str,
        *,
        preload_template: str | None = None,
        script_tag_template: str | None = None,
        init_template: str | None = None,
        chunk_template: str | None = None,
        policy: ScriptInjectionPolicy = ScriptInjectionPolicy.AUTO,
    ) -> None:
        """Store the URL, tag templates, and injection policy.

        `usable_template` replaces a template that cannot format with the default.
        """
        self._url = next_js_url
        self._preload_template = usable_template(
            preload_template or self.DEFAULT_PRELOAD,
            self.DEFAULT_PRELOAD,
            TAG_FIELDS,
            "NEXT_JS_OPTIONS['preload_template']",
        )
        self._script_tag_template = usable_template(
            script_tag_template or self.DEFAULT_SCRIPT_TAG,
            self.DEFAULT_SCRIPT_TAG,
            TAG_FIELDS,
            "NEXT_JS_OPTIONS['script_tag_template']",
        )
        self._init_template = usable_template(
            init_template or self.DEFAULT_INIT,
            self.DEFAULT_INIT,
            INIT_FIELDS,
            "NEXT_JS_OPTIONS['init_template']",
        )
        self._chunk_template = _script_chunk(
            usable_template(
                chunk_template or self.DEFAULT_CHUNK,
                self.DEFAULT_CHUNK,
                CHUNK_FIELDS,
                CHUNK_TEMPLATE,
            ),
            self.DEFAULT_CHUNK,
        )
        self._policy = policy

    @property
    def policy(self) -> ScriptInjectionPolicy:
        """Return the configured script injection policy."""
        return self._policy

    @property
    def url(self) -> str:
        """Return the resolved `next.min.js` URL."""
        return self._url

    def preload_link(self, url: str | None = None, *, nonce: str | None = None) -> str:
        """Return the preload hint tag, an explicit `url` replaces the runtime URL."""
        return self._preload_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )

    def script_tag(self, url: str | None = None, *, nonce: str | None = None) -> str:
        """Return the blocking script tag, an explicit `url` replaces the runtime's."""
        return self._script_tag_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )

    def bundle_tag(
        self, url: str, *, load: BundleLoad, nonce: str | None = None
    ) -> str:
        """Return the script tag of a declared bundle with the attributes of `load`.

        The load attributes follow `<script`, or the default tag's without that.
        """
        tag = self.script_tag(url, nonce=nonce)
        opening = _SCRIPT_OPEN.search(tag)
        if opening is None:
            tag = self.DEFAULT_SCRIPT_TAG.format(
                url=escape(url), nonce_attr=nonce_attr(nonce)
            )
            at = len(_SCRIPT_START)
        else:
            at = opening.end()
        return f"{tag[:at]}{load.value}{tag[at:]}"

    def init_script(
        self,
        js_context: Mapping[str, Any],
        *,
        key_serializers: Mapping[str, JsContextSerializer] | None = None,
        encoded: Mapping[str, str] | None = None,
        nonce: str | None = None,
    ) -> str:
        """Return the inline script that passes the context to `Next._init`.

        The payload is escaped so a `</script>` inside it cannot close the element.
        """
        default = resolve_serializer()
        serializers = key_serializers or {}
        fragments: list[str] = []
        for k, v in js_context.items():
            frag = encoded.get(k) if encoded is not None else None
            if frag is None:
                frag = serializers.get(k, default).dumps(v)
            fragments.append(f"{_encoded_key(k)}:{frag}")
        if CHUNKS_PAYLOAD_KEY in js_context:
            if entry := realtime_offer().entry:
                fragments.append(entry)
            if partial_streams.get():
                fragments.append(f"{_encoded_key(STREAM_PAYLOAD_KEY)}:true")
            routed = router_render.get()
            if routed is not None:
                fragments.append(
                    f"{_encoded_key(ROUTER_PAYLOAD_KEY)}:{routed.encoded()}"
                )
                fragments.append(f"{_encoded_key(LAYOUTS_PAYLOAD_KEY)}:{routed.mark}")
        payload = "{" + ",".join(fragments) + "}"
        if _ESCAPED.search(payload) is not None:
            payload = payload.translate(SCRIPT_ESCAPES)
        return self._init_template.format(payload=payload, nonce_attr=nonce_attr(nonce))

    def chunk_script(self, payload: str, *, nonce: str | None = None) -> str:
        """Return the inline script a streamed page pushes one serialised chunk with.

        The payload is escaped so no markup or line separator inside it ends the script.
        """
        if _ESCAPED.search(payload) is not None:
            payload = payload.translate(SCRIPT_ESCAPES)
        return self._chunk_template.format(
            payload=payload, nonce_attr=nonce_attr(nonce)
        )

    @classmethod
    def from_options(
        cls, next_js_url: str, options: Mapping[str, Any] | None = None
    ) -> NextScriptBuilder:
        """Build a script builder from an options mapping.

        `policy` accepts a `ScriptInjectionPolicy` member or its string value.
        """
        options = options or {}
        raw_policy = options.get("policy", ScriptInjectionPolicy.AUTO)
        if isinstance(raw_policy, ScriptInjectionPolicy):
            policy = raw_policy
        else:
            try:
                policy = ScriptInjectionPolicy(raw_policy)
            except ValueError as e:
                allowed = ", ".join(repr(p.value) for p in ScriptInjectionPolicy)
                msg = (
                    f"Invalid NextScriptBuilder policy {raw_policy!r}. "
                    f"Expected one of {allowed}"
                )
                raise ValueError(msg) from e
        return cls(
            next_js_url,
            preload_template=options.get("preload_template"),
            script_tag_template=options.get("script_tag_template"),
            init_template=options.get("init_template"),
            chunk_template=options.get("chunk_template"),
            policy=policy,
        )
