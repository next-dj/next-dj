"""Pluggable builder for the `next.min.js` preload, script, and init tags.

The builder produces the three HTML fragments that wire `window.Next`
into the rendered page. The first fragment is a preload hint injected
before `</head>` so the browser starts downloading during HTML parsing.
The second fragment is a blocking script tag for the compiled runtime.
The third fragment is an inline script that feeds the serialized JS
context into `Next._init`.

Every template is an instance attribute, so users can override any
single tag without subclassing. An injection policy controls whether
the static manager emits those tags at all.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, ClassVar, Final


if TYPE_CHECKING:
    from collections.abc import Mapping


NEXT_JS_STATIC_PATH: Final = "next/next.min.js"


class ScriptInjectionPolicy(enum.Enum):
    """Controls whether `next.min.js` is automatically injected.

    The `AUTO` value is the default. Under `AUTO` the static manager
    emits the preload hint, the `<script>` tag, and the `Next._init`
    call into every rendered page. The `DISABLED` value skips injection
    entirely and is useful when a page does not need `window.Next`, for
    example a raw API response rendered through the page machinery. The
    `MANUAL` value skips automatic injection but still builds the
    fragments on request so users can emit the tags themselves from a
    template.
    """

    AUTO = "auto"
    DISABLED = "disabled"
    MANUAL = "manual"


class NextScriptBuilder:
    """Builds the preload hint, script tag, and init script for `window.Next`.

    The `next_js_url` argument is the public URL of the compiled
    `next.min.js` asset. The optional `preload_template`,
    `script_tag_template`, and `init_template` arguments override the
    defaults. The preload and script templates must contain the `{url}`
    placeholder. The init template must contain the `{payload}`
    placeholder, which receives the JSON-serialized JS context. The
    `policy` argument is consulted by the static manager before
    injection and defaults to `ScriptInjectionPolicy.AUTO`.
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

    def preload_link(self) -> str:
        """Return the preload hint tag for early browser download."""
        return self._preload_template.format(url=self._url)

    def script_tag(self) -> str:
        """Return the blocking script tag that executes `next.min.js`."""
        return self._script_tag_template.format(url=self._url)

    def init_script(self, js_context: Mapping[str, Any]) -> str:
        """Return the inline script that passes the context to `Next._init`.

        Delegates serialisation to the configured `JsContextSerializer`
        so the init payload honours the same encoding rules as values
        registered through `StaticCollector.add_js_context`.
        """
        from .serializers import resolve_serializer  # noqa: PLC0415

        payload = resolve_serializer().dumps(dict(js_context))
        return self._init_template.format(payload=payload)

    @classmethod
    def from_options(
        cls,
        next_js_url: str,
        options: Mapping[str, Any] | None = None,
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
