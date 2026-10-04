"""Build the `next.min.js` preload, script and init tags, and name its lazy chunks.

The tag templates are constructor arguments, so a project overrides them without
subclassing. An injection policy decides whether the tags are emitted.
"""

from __future__ import annotations

import enum
import functools
import json
import logging
import re
from html import escape
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final

from next.caches import DEFAULT_CACHE_SIZE
from next.csrf import csrf_payload
from next.diagnostics import FailureLog

from .serializers import resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from django.http import HttpRequest

    from .serializers import JsContextSerializer


logger = logging.getLogger(__name__)

_failures = FailureLog(logger)

NEXT_JS_STATIC_PATH: Final = "next/next.min.js"

SCRIPTS_CHUNK_STATIC_PATH: Final = "next/next.scripts.min.js"
"""The optional chunk carrying consent and third-party scripts."""

DEV_CHUNK_STATIC_PATH: Final = "next/next.dev.min.js"
"""The diagnostics chunk the runtime fetches only when the payload carries `$dev`."""

SSE_CHUNK_STATIC_PATH: Final = "next/next.sse.min.js"
"""The server-sent events bridge, fetched once a page marks an element to stream."""

CSRF_CHUNK_STATIC_PATH: Final = "next/next.csrf.min.js"
"""The deferred CSRF token loader, fetched on the first post from a page without one."""

POLL_CHUNK_STATIC_PATH: Final = "next/next.poll.min.js"
"""The zone poller, fetched once a page marks a zone to poll."""

DEV_CHUNK_KEY: Final = "dev"
"""The `$chunks` key of the diagnostics chunk, listed only under `DEBUG`."""

CHUNK_STATIC_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "scripts": SCRIPTS_CHUNK_STATIC_PATH,
        "sse": SSE_CHUNK_STATIC_PATH,
        "csrf": CSRF_CHUNK_STATIC_PATH,
        "poll": POLL_CHUNK_STATIC_PATH,
        DEV_CHUNK_KEY: DEV_CHUNK_STATIC_PATH,
    }
)
"""Every lazy chunk by the `$chunks` key the runtime fetches it under."""

CSRF_PAYLOAD_KEY: Final = "$csrf"

# Sent only under `DEBUG`, so a production payload never requests the dev chunk.
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


def csrf_payload_for(request: HttpRequest | None) -> dict[str, str] | None:
    """Return the `$csrf` payload, or None when the request cannot mint a token.

    A request whose `META` is not a dict, such as a test double, yields no payload.
    """
    if request is None or not isinstance(getattr(request, "META", None), dict):
        return None
    return csrf_payload(request)


TAG_FIELDS: Final = ("url", "nonce_attr")
"""The fields a tag template naming an asset is formatted with."""

INIT_FIELDS: Final = ("payload", "nonce_attr")
"""The fields the init script template is formatted with."""

TEMPLATE_ERRORS: Final = (KeyError, IndexError, ValueError, AttributeError)
"""The exceptions `str.format` raises for an unbalanced brace or an unknown field."""


def dry_run_template(template: str, fields: Iterable[str]) -> None:
    """Format `template` with a blank value per field, raising what a render would."""
    template.format(**dict.fromkeys(fields, ""))


def usable_template(
    template: str, default: str, fields: Iterable[str], where: str
) -> str:
    """Return `template` when it formats with `fields`, else `default`.

    A template that cannot format would fail every render, so the default replaces it.
    The error is raised under `DEBUG` and otherwise logged at the `FailureLog` rate.
    The default itself is known to format, so it is returned without a dry run.
    """
    if template == default:
        return default
    try:
        dry_run_template(template, fields)
    except TEMPLATE_ERRORS as exc:
        _failures.contain(
            exc,
            where,
            "%s %r does not format with the fields %s, so the default tag renders "
            "instead. Double every literal brace as {{ or }} and name no other field.",
            where,
            template,
            ", ".join(f"{{{name}}}" for name in fields),
        )
        return default
    return template


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

    `preload_template`, `script_tag_template` and `init_template` override the
    defaults. The first two take `{url}`, the last takes `{payload}`, and all three
    take `{nonce_attr}`.
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

        An explicit `url` replaces the resolved runtime URL, so the injector can pass
        the URL a request-aware backend rewrote.
        """
        return self._preload_template.format(
            url=escape(str(url or self._url)), nonce_attr=nonce_attr(nonce)
        )

    def script_tag(self, url: str | None = None, *, nonce: str | None = None) -> str:
        """Return the blocking script tag that executes `next.min.js`.

        An explicit `url` replaces the resolved runtime URL, so the injector can pass
        the URL a request-aware backend rewrote.
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

        A value in `encoded` is reused instead of serialised again. The payload is
        escaped so a `</script>` inside it cannot close the element.
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
