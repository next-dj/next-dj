"""Pluggable builder for the `next.min.js` preload, script, and init tags.

Every template is an instance attribute, overridable without subclassing, and an
injection policy decides whether the tags are emitted at all.
"""

from __future__ import annotations

import enum
import functools
import json
import re
from html import escape
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final

from next.caches import DEFAULT_CACHE_SIZE
from next.csrf import csrf_payload

from .serializers import resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest

    from .serializers import JsContextSerializer


NEXT_JS_STATIC_PATH: Final = "next/next.min.js"

SCRIPTS_CHUNK_STATIC_PATH: Final = "next/next.scripts.min.js"
"""The optional chunk carrying consent and third-party scripts."""

DEV_CHUNK_STATIC_PATH: Final = "next/next.dev.min.js"
"""The diagnostics chunk the runtime fetches only when the payload carries `$dev`."""

SSE_CHUNK_STATIC_PATH: Final = "next/next.sse.min.js"
"""The server-sent events bridge, fetched once a page marks an element to stream."""

CSRF_CHUNK_STATIC_PATH: Final = "next/next.csrf.min.js"
"""The deferred CSRF minter, fetched once a page that carries no token posts."""

POLL_CHUNK_STATIC_PATH: Final = "next/next.poll.min.js"
"""The zone poller, fetched once a page marks a zone to poll."""

CHUNK_STATIC_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "scripts": SCRIPTS_CHUNK_STATIC_PATH,
        "sse": SSE_CHUNK_STATIC_PATH,
        "csrf": CSRF_CHUNK_STATIC_PATH,
        "poll": POLL_CHUNK_STATIC_PATH,
        "dev": DEV_CHUNK_STATIC_PATH,
    }
)
"""Every lazy chunk by the `$chunks` key the runtime fetches it under."""

CSRF_PAYLOAD_KEY: Final = "$csrf"

# Present in the init payload only under `DEBUG`, so production carries no dev bytes.
DEV_PAYLOAD_KEY: Final = "$dev"

CHUNKS_PAYLOAD_KEY: Final = "$chunks"
SCRIPTS_PAYLOAD_KEY: Final = "$scripts"
CONSENT_PAYLOAD_KEY: Final = "$consent"

CHUNK_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {SCRIPTS_PAYLOAD_KEY, CONSENT_PAYLOAD_KEY}
)
"""The payload keys that make the runtime fetch the scripts chunk."""

RESERVED_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {CSRF_PAYLOAD_KEY, DEV_PAYLOAD_KEY, CHUNKS_PAYLOAD_KEY, *CHUNK_PAYLOAD_KEYS}
)

SCRIPT_ESCAPES: Final[dict[int, str]] = {
    ord("<"): "\\u003C",
    ord(">"): "\\u003E",
    ord("&"): "\\u0026",
    ord("\u2028"): "\\u2028",
    ord("\u2029"): "\\u2029",
}
"""Escapes for JSON inside a `<script>`, mirroring Django's `json_script`.

The code points only appear inside JSON strings, so escaping them changes nothing.
"""

_ESCAPED: Final = re.compile(f"[{re.escape(''.join(map(chr, SCRIPT_ESCAPES)))}]")
"""Finds a character `SCRIPT_ESCAPES` rewrites, a scan far cheaper than `translate`."""


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def _encoded_key(key: str) -> str:
    """Return a payload key as JSON, since `json.dumps` builds an encoder per call."""
    return json.dumps(key, separators=(",", ":"))


def nonce_attr(nonce: str | None) -> str:
    """Return the ` nonce="..."` attribute a tag carries, empty without a nonce."""
    return f' nonce="{escape(nonce)}"' if nonce else ""


def csrf_payload_for(request: HttpRequest | None) -> dict[str, str] | None:
    """Return the `$csrf` payload, or None when the request cannot mint a token.

    A request whose `META` is not a real mapping yields no payload, so a render from a
    test stand-in stays byte-identical to the pre-partial output.
    """
    if request is None or not isinstance(getattr(request, "META", None), dict):
        return None
    return csrf_payload(request)


class ScriptInjectionPolicy(enum.Enum):
    """Controls whether `next.min.js` is automatically injected.

    `AUTO` emits every tag automatically. `DISABLED` skips injection entirely.
    `MANUAL` still builds the fragments but leaves emitting them to the template.
    """

    AUTO = "auto"
    DISABLED = "disabled"
    MANUAL = "manual"


class NextScriptBuilder:
    """Builds the preload hint, script tag, and init script for `window.Next`.

    `preload_template`, `script_tag_template`, and `init_template` override the
    defaults, the first two needing `{url}`, the last `{payload}`, all `{nonce_attr}`.
    """

    DEFAULT_PRELOAD: ClassVar[str] = (
        '<link rel="preload" as="script" href="{url}"{nonce_attr}>'
    )
    DEFAULT_SCRIPT_TAG: ClassVar[str] = '<script src="{url}"{nonce_attr}></script>'
    DEFAULT_INIT: ClassVar[str] = "<script{nonce_attr}>Next._init({payload});</script>"

    def __init__(
        self,
        next_js_url: str,
        *,
        preload_template: str | None = None,
        script_tag_template: str | None = None,
        init_template: str | None = None,
        policy: ScriptInjectionPolicy = ScriptInjectionPolicy.AUTO,
    ) -> None:
        """Store the URL, tag templates, and injection policy."""
        self._url = next_js_url
        self._preload_template = preload_template or self.DEFAULT_PRELOAD
        self._script_tag_template = script_tag_template or self.DEFAULT_SCRIPT_TAG
        self._init_template = init_template or self.DEFAULT_INIT
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
        """Return the preload hint tag for early browser download.

        The optional `url` overrides the resolved runtime URL, which lets the
        static manager pass the answer of a request-aware backend.
        """
        return self._preload_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )

    def script_tag(self, url: str | None = None, *, nonce: str | None = None) -> str:
        """Return the blocking script tag that executes `next.min.js`.

        The optional `url` overrides the resolved runtime URL, which lets the
        static manager pass the answer of a request-aware backend.
        """
        return self._script_tag_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )

    def init_script(
        self,
        js_context: Mapping[str, Any],
        *,
        key_serializers: Mapping[str, JsContextSerializer] | None = None,
        encoded: Mapping[str, str] | None = None,
        nonce: str | None = None,
    ) -> str:
        """Return the inline script that passes the context to `Next._init`.

        An already-encoded value is reused rather than serialised twice, and the payload
        is escaped so a `</script>` inside it cannot break out of the element.
        """
        default = resolve_serializer()
        serializers = key_serializers or {}
        fragments: list[str] = []
        for k, v in js_context.items():
            frag = encoded.get(k) if encoded is not None else None
            if frag is None:
                frag = serializers.get(k, default).dumps(v)
            fragments.append(f"{_encoded_key(k)}:{frag}")
        payload = "{" + ",".join(fragments) + "}"
        if _ESCAPED.search(payload) is not None:
            payload = payload.translate(SCRIPT_ESCAPES)
        return self._init_template.format(payload=payload, nonce_attr=nonce_attr(nonce))

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
            policy=policy,
        )
