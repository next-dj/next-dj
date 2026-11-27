"""Build the `next.min.js` preload, script and init tags, and name its lazy chunks."""

from __future__ import annotations

import enum
import json
import logging
import re
from contextvars import ContextVar
from html import escape
from html.parser import HTMLParser
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final, override

from next.caches import DEFAULT_CACHE_SIZE, pure_cache
from next.csrf import csrf_payload
from next.diagnostics import FailureLog

from .serializers import resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from django.http import HttpRequest

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

FORMS_CHUNK_STATIC_PATH: Final = "next/next.forms.min.js"
"""Inline validation and filter forms, fetched once a page holds a form using them."""

LAYERS_CHUNK_STATIC_PATH: Final = "next/next.layers.min.js"
"""The modal layers, fetched once a page holds a layer link or an envelope opens one."""

BOUNDARIES_CHUNK_STATIC_PATH: Final = "next/next.boundaries.min.js"
"""The stream reader and the client trip, declared by a page that streams or defers."""

RELOAD_CHUNK_STATIC_PATH: Final = "next/next.reload.min.js"
"""The zone and layer skeletons, declared by a page holding a zone or a layer link."""

MORE_CHUNK_STATIC_PATH: Final = "next/next.more.min.js"
"""The `{% more %}` URL sync and pruning, declared by a page holding such a link."""

ROUTER_CHUNK_STATIC_PATH: Final = "next/next.router.min.js"
"""The client router with its history, declared by every routed document."""

INTENT_CHUNK_STATIC_PATH: Final = "next/next.intent.min.js"
"""The link and layer prefetch, its cache and active links, loaded once idle."""

DEV_CHUNK_KEY: Final = "dev"
"""The `$chunks` key of the diagnostics chunk, listed only under `DEBUG`."""

WS_CHUNK_KEY: Final = "ws"
"""The `$chunks` key of the WebSocket adapter, listed only while `ws` is active."""

BOUNDARIES_CHUNK_KEY: Final = "boundaries"
"""The need of a page that streams, holds a client trip or offers a boundary retry."""

RELOAD_CHUNK_KEY: Final = "reload"
MORE_CHUNK_KEY: Final = "more"
ROUTER_CHUNK_KEY: Final = "router"
INTENT_CHUNK_KEY: Final = "intent"

DECLARED_CHUNK_KEYS: Final = frozenset(
    {
        BOUNDARIES_CHUNK_KEY,
        RELOAD_CHUNK_KEY,
        MORE_CHUNK_KEY,
        ROUTER_CHUNK_KEY,
        INTENT_CHUNK_KEY,
    }
)
"""The needs a page names by script tag or in `$router`, never in `$chunks`."""

CHUNK_STATIC_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "scripts": SCRIPTS_CHUNK_STATIC_PATH,
        "stream": STREAM_CHUNK_STATIC_PATH,
        WS_CHUNK_KEY: WS_CHUNK_STATIC_PATH,
        "csrf": CSRF_CHUNK_STATIC_PATH,
        "poll": POLL_CHUNK_STATIC_PATH,
        "forms": FORMS_CHUNK_STATIC_PATH,
        "layers": LAYERS_CHUNK_STATIC_PATH,
        DEV_CHUNK_KEY: DEV_CHUNK_STATIC_PATH,
        BOUNDARIES_CHUNK_KEY: BOUNDARIES_CHUNK_STATIC_PATH,
        RELOAD_CHUNK_KEY: RELOAD_CHUNK_STATIC_PATH,
        MORE_CHUNK_KEY: MORE_CHUNK_STATIC_PATH,
        ROUTER_CHUNK_KEY: ROUTER_CHUNK_STATIC_PATH,
        INTENT_CHUNK_KEY: INTENT_CHUNK_STATIC_PATH,
    }
)
"""The bundle of each name a page lists or declares."""

LISTED_CHUNK_KEYS: Final = tuple(
    name for name in CHUNK_STATIC_PATHS if name not in DECLARED_CHUNK_KEYS
)
"""The chunks `$chunks` may list, every shipped one but the declared bundles."""

OPTIONAL_CHUNK_KEYS: Final = frozenset({DEV_CHUNK_KEY, WS_CHUNK_KEY})
"""The listed chunks `$chunks` names only while a render switches them on."""

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

VERSION_PAYLOAD_KEY: Final = "$version"
"""The asset version the runtime asserts on its first request, not taught by a reply."""

PROTOCOL_PAYLOAD_KEY: Final = "$protocol"
"""The wire protocol of the server, which a runtime from another release refuses."""

ROUTER_PAYLOAD_KEY: Final = "$router"
"""The settings the client router reads, present only while the router is on."""

INIT_ATTR: Final = "data-next-init"
"""The mark the injector writes on the init script, whatever its template spells."""

_CHUNKS_FIELD: Final = f'"{CHUNKS_PAYLOAD_KEY}":'
_PRELOAD: Final = "preload"
_SCRIPT: Final = "script"
_ENTRIES_HELD: Final = 64

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
        VERSION_PAYLOAD_KEY,
        PROTOCOL_PAYLOAD_KEY,
        ROUTER_PAYLOAD_KEY,
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


def _script_safe(text: str) -> str:
    """Return `text` with every character `SCRIPT_ESCAPES` names escaped.

    The escapes rewrite single characters, so escaped parts join into an escaped whole.
    """
    if _ESCAPED.search(text) is None:
        return text
    return text.translate(SCRIPT_ESCAPES)


@pure_cache(DEFAULT_CACHE_SIZE)
def _encoded_key(key: str) -> str:
    """Return a payload key encoded as JSON, memoised as `json.dumps` is costly."""
    return json.dumps(key, separators=(",", ":"))


def nonce_attr(nonce: str | None) -> str:
    """Return the ` nonce="..."` attribute a tag carries, empty without a nonce."""
    return f' nonce="{escape(nonce)}"' if nonce else ""


def payload_entry(key: str, encoded: str) -> str:
    """Return one init-payload entry, the JSON key of `key` before `encoded`."""
    return f"{_encoded_key(key)}:{encoded}"


def csrf_payload_for(request: HttpRequest | None) -> dict[str, str] | None:
    """Return the `$csrf` payload, or None when the request cannot mint a token.

    A request whose `META` is not a dict, such as a test double, yields no payload.
    """
    if request is None or not isinstance(getattr(request, "META", None), dict):
        return None
    return csrf_payload(request)


html_finish: ContextVar[Callable[[str], str] | None] = ContextVar(
    "next_html_finish", default=None
)
"""The final pass the view runs over the HTML it sends, unless a provider does."""


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


_NAME_END: Final = r"(?=[\t\n\f\r />])"
SCRIPT_TEXT: Final = re.compile(
    rf"<script{_NAME_END}[^>]*>(?P<text>.*?)</script{_NAME_END}[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
"""A `<script>` element with its text under `text`, its tag names ended as HTML does."""
_PROBE: Final = "\ue000"
_SCRIPT_START: Final = "<script"
_SCRIPT_OPEN: Final = re.compile(rf"{_SCRIPT_START}{_NAME_END}", re.IGNORECASE)


_RUNS: Final = re.compile(r"^\s*$|module|script", re.IGNORECASE)
_INERT_TAGS: Final = frozenset({"template", "noscript"})


class _Dropped(HTMLParser):
    """Find the first script a swap drops, outside the template and noscript content."""

    def __init__(self, nonce: str | None) -> None:
        super().__init__(convert_charrefs=False)
        self.nonce = nonce
        self.inert = 0
        self.found: str | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INERT_TAGS:
            self.inert += 1
        elif tag == "script" and not self.inert and self.found is None:
            named = dict(attrs)
            if not _RUNS.search(named.get("type") or ""):
                return
            if "src" in named or not self.nonce or named.get("nonce") != self.nonce:
                self.found = self.get_starttag_text()

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in _INERT_TAGS and self.inert:
            self.inert -= 1


def dropped_script(html: str, nonce: str | None) -> str | None:
    """Return the opening tag of the first script a soft navigation would not run.

    The client runs an inline script of a swap only under the response `nonce`, so a
    `src` script or an inline one without that nonce is lost unless the page loads.
    """
    if _SCRIPT_OPEN.search(html) is None:
        return None
    parser = _Dropped(nonce)
    parser.feed(html)
    parser.close()
    return parser.found


def payload_in_script(template: str) -> bool:
    """Whether every `{payload}` of a chunk template lands in the text of a `<script>`.

    The payload is escaped for script text only, which an attribute does not hold.
    """
    try:
        rendered = template.format(payload=_PROBE, nonce_attr="")
    except TEMPLATE_ERRORS:
        return True
    texts = SCRIPT_TEXT.finditer(rendered)
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


_INIT_OPEN: Final = re.compile(rf"{_SCRIPT_START}(?=[\t\n\f\r />{{])", re.IGNORECASE)


def _marked(template: str) -> str:
    """Return the init `template` with `INIT_ATTR` on its first `<script` tag.

    A router reading a foreign document finds the init script by the mark alone.
    """
    opening = _INIT_OPEN.search(template)
    if opening is None or INIT_ATTR in template:
        return template
    at = opening.end()
    return f"{template[:at]} {INIT_ATTR}{template[at:]}"


class BundleLoad(enum.Enum):
    """How the tag of a declared bundle loads it, each value the attributes it adds."""

    LAZY = ' async fetchpriority="low"'
    EAGER = " async"
    DEFERRED = " defer"


def bundle_load(*, streams: bool, urgent: bool) -> BundleLoad:
    """Return how a declared bundle loads, hurried by a `load` boundary.

    `defer` waits for the whole document, which on a stream arrives with its last chunk.
    """
    if streams and urgent:
        return BundleLoad.EAGER
    if urgent:
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
    DEFAULT_INIT: ClassVar[str] = (
        f"<script {INIT_ATTR}{{nonce_attr}}>Next._init({{payload}});</script>"
    )
    # The script removes itself once it has pushed, so neither the parse nor the asset
    # scan of each apply keeps walking every chunk the stream has delivered.
    DEFAULT_CHUNK: ClassVar[str] = (
        "<script{nonce_attr}>(self.$nx||=[]).push({payload});"
        "document.currentScript?.remove()</script>"
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
        self._init_template = _marked(
            usable_template(
                init_template or self.DEFAULT_INIT,
                self.DEFAULT_INIT,
                INIT_FIELDS,
                "NEXT_JS_OPTIONS['init_template']",
            )
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
        self._entries: dict[tuple[str, ...], str] = {}
        self._tags: dict[tuple[object, str | None], str] = {}

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
        key = (_PRELOAD, url)
        if nonce is None and (held := self._tags.get(key)) is not None:
            return held
        tag = self._preload_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )
        return tag if nonce is not None else self._held(key, tag)

    def script_tag(self, url: str | None = None, *, nonce: str | None = None) -> str:
        """Return the blocking script tag, an explicit `url` replaces the runtime's."""
        key = (_SCRIPT, url)
        if nonce is None and (held := self._tags.get(key)) is not None:
            return held
        tag = self._script_tag_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )
        return tag if nonce is not None else self._held(key, tag)

    def _held(self, key: tuple[object, str | None], tag: str) -> str:
        """Keep a tag without a nonce, which every page of one URL repeats."""
        if len(self._tags) >= _ENTRIES_HELD:
            self._tags.clear()
        self._tags[key] = tag
        return tag

    def bundle_tag(
        self, url: str, *, load: BundleLoad, nonce: str | None = None
    ) -> str:
        """Return the script tag of a declared bundle with the attributes of `load`.

        The load attributes follow `<script`, or the default tag's without that.
        """
        key = (load, url)
        if nonce is None and (held := self._tags.get(key)) is not None:
            return held
        tag = self.script_tag(url, nonce=nonce)
        opening = _SCRIPT_OPEN.search(tag)
        if opening is None:
            tag = self.DEFAULT_SCRIPT_TAG.format(
                url=escape(url), nonce_attr=nonce_attr(nonce)
            )
            at = len(_SCRIPT_START)
        else:
            at = opening.end()
        tag = f"{tag[:at]}{load.value}{tag[at:]}"
        return tag if nonce is not None else self._held(key, tag)

    def init_script(
        self,
        js_context: Mapping[str, Any],
        *,
        key_serializers: Mapping[str, JsContextSerializer] | None = None,
        encoded: Mapping[str, str] | None = None,
        shared: Sequence[str] = (),
        nonce: str | None = None,
    ) -> str:
        """Return the inline script that passes the context to `Next._init`.

        `shared` holds the encoded provider entries that follow `$chunks`. The payload
        is escaped so a `</script>` inside it cannot close the element.
        """
        default = resolve_serializer()
        serializers = key_serializers or {}
        fragments: list[str] = []
        chunks: str | None = None
        for k, v in js_context.items():
            frag = encoded.get(k) if encoded is not None else None
            if frag is None:
                frag = serializers.get(k, default).dumps(v)
            if k == CHUNKS_PAYLOAD_KEY:
                chunks = frag
            else:
                fragments.append(f"{_encoded_key(k)}:{frag}")
        payload = _script_safe(",".join(fragments))
        if chunks is not None:
            fixed = self._fixed((chunks, *shared))
            payload = f"{payload},{fixed}" if payload else fixed
        return self._init_template.format(
            payload=f"{{{payload}}}", nonce_attr=nonce_attr(nonce)
        )

    def _fixed(self, key: tuple[str, ...]) -> str:
        """Return `$chunks` and the shared provider entries, escaped once per value.

        A page pays only for the entries of its own request.
        """
        held = self._entries.get(key)
        if held is None:
            chunks, *shared = key
            held = _script_safe(",".join((f"{_CHUNKS_FIELD}{chunks}", *shared)))
            if len(self._entries) >= _ENTRIES_HELD:
                self._entries.clear()
            self._entries[key] = held
        return held

    def chunk_script(self, payload: str, *, nonce: str | None = None) -> str:
        """Return the inline script a streamed page pushes one serialised chunk with.

        The payload is escaped so no markup or line separator inside it ends the script.
        """
        return self._chunk_template.format(
            payload=_script_safe(payload), nonce_attr=nonce_attr(nonce)
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
