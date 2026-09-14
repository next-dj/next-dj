"""Pluggable builder for the `next.min.js` preload, script, and init tags.

The preload hint goes before `</head>` so the download starts during parsing, a blocking
script tag loads the runtime, and an inline script feeds the JS context to `Next._init`.

Every template is an instance attribute, so a single tag is overridable without
subclassing, and an injection policy decides whether the tags are emitted at all.
"""

from __future__ import annotations

import enum
import json
from typing import TYPE_CHECKING, Any, ClassVar, Final

from django.conf import settings
from django.http.request import HttpHeaders
from django.middleware.csrf import get_token

from .serializers import resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest

    from .serializers import JsContextSerializer


NEXT_JS_STATIC_PATH: Final = "next/next.min.js"

CSRF_PAYLOAD_KEY: Final = "$csrf"

# Present in the init payload only under `DEBUG`, so production carries no dev bytes.
DEV_PAYLOAD_KEY: Final = "$dev"

# The init-payload keys the framework owns. A colliding js-context key never
# reaches the payload, so the key means one thing in every environment.
RESERVED_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {CSRF_PAYLOAD_KEY, DEV_PAYLOAD_KEY}
)

# Escapes for the HTML `<script>` context, mirroring Django's `json_script`. The
# code points only appear inside JSON strings, so escaping them changes nothing.
_SCRIPT_ESCAPES: Final[dict[int, str]] = {
    ord("<"): "\\u003C",
    ord(">"): "\\u003E",
    ord("&"): "\\u0026",
    ord("\u2028"): "\\u2028",
    ord("\u2029"): "\\u2029",
}


def csrf_header_name() -> str:
    """Return the CSRF header name in HTTP wire form from Django settings.

    Django stores `CSRF_HEADER_NAME` in WSGI `request.META` form, for
    example `HTTP_X_CSRFTOKEN`. The runtime sends the header by its HTTP
    name, so the META form is unmangled with the same rule Django uses
    to expose headers. The cookie is never read.
    """
    raw = settings.CSRF_HEADER_NAME
    name = HttpHeaders.parse_header_name(raw)
    if name is not None:
        return name
    return raw.removeprefix(HttpHeaders.HTTP_PREFIX).replace("_", "-").title()


def csrf_payload(request: HttpRequest) -> dict[str, str]:
    """Return the `$csrf` payload of header name and token for `Next._init`."""
    return {"header": csrf_header_name(), "token": get_token(request)}


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

    The `AUTO` value is the default. Under `AUTO` the static manager emits the preload
    hint, the `<script>` tag, and the `Next._init` call into every rendered page. The
    `DISABLED` value skips injection entirely and is useful when a page does not need
    `window.Next`, for example a raw API response rendered through the page machinery.
    The `MANUAL` value skips automatic injection but still builds the fragments on
    request so users can emit the tags themselves from a template.
    """

    AUTO = "auto"
    DISABLED = "disabled"
    MANUAL = "manual"


class NextScriptBuilder:
    """Builds the preload hint, script tag, and init script for `window.Next`.

    `preload_template`, `script_tag_template`, and `init_template` override the
    defaults, the first two needing `{url}` and the last `{payload}`.
    """

    DEFAULT_PRELOAD: ClassVar[str] = '<link rel="preload" as="script" href="{url}">'
    DEFAULT_SCRIPT_TAG: ClassVar[str] = '<script src="{url}"></script>'
    DEFAULT_INIT: ClassVar[str] = "<script>Next._init({payload});</script>"

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

    def preload_link(self, url: str | None = None) -> str:
        """Return the preload hint tag for early browser download.

        The optional `url` overrides the resolved runtime URL, which lets the
        static manager pass the answer of a request-aware backend.
        """
        return self._preload_template.format(url=url or self._url)

    def script_tag(self, url: str | None = None) -> str:
        """Return the blocking script tag that executes `next.min.js`.

        The optional `url` overrides the resolved runtime URL, which lets the
        static manager pass the answer of a request-aware backend.
        """
        return self._script_tag_template.format(url=url or self._url)

    def init_script(
        self,
        js_context: Mapping[str, Any],
        *,
        key_serializers: Mapping[str, JsContextSerializer] | None = None,
        encoded: Mapping[str, str] | None = None,
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
            encoded_key = json.dumps(k, separators=(",", ":"))
            fragments.append(f"{encoded_key}:{frag}")
        payload = "{" + ",".join(fragments) + "}"
        payload = payload.translate(_SCRIPT_ESCAPES)
        return self._init_template.format(payload=payload)

    @classmethod
    def from_options(
        cls, next_js_url: str, options: Mapping[str, Any] | None = None
    ) -> NextScriptBuilder:
        """Build a script builder from an options mapping.

        The recognised keys are `preload_template`, `script_tag_template`,
        `init_template`, and `policy`. The `policy` value may be a
        `ScriptInjectionPolicy` member or the string value of one of its
        members. Any other value raises `ValueError`.
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
