"""Wire-key constants of the partial protocol, shared by every area that writes them.

Frozen wire contract mirrored in next/client/protocol.ts and apply.ts.
"""

from typing import Final


VERSION: Final = "version"
OPS: Final = "ops"
ASSETS: Final = "assets"
FORM: Final = "form"
CSRF: Final = "csrf"
REQUEST_ID: Final = "request_id"
TOPIC: Final = "topic"
SEQ: Final = "seq"
EPOCH: Final = "epoch"
SERVER_TIME: Final = "server_time"

SSE_HELLO: Final = "next-hello"
SSE_SKIP: Final = "next-skip"
SSE_END: Final = "next-end"

OP: Final = "op"
TARGET: Final = "target"
HTML: Final = "html"

KIND: Final = "kind"
URL: Final = "url"
INLINE: Final = "inline"
LOAD: Final = "load"
SCOPE: Final = "scope"
"""The asset field naming who owns a stylesheet, `PAGE_SCOPE` for one a page renders."""

PAGE_SCOPE: Final = "page"
"""The `scope` of a stylesheet the client router retires once a page drops it."""

UID: Final = "uid"
VALID: Final = "valid"
ERRORS: Final = "errors"

ZONE: Final = "zone"
BOUNDARY: Final = "boundary"
SLOT: Final = "slot"
"""The morph target naming the slot of one layout by its id, a range between markers."""

BODY: Final = "body"
"""The morph target `{"body": true}` naming the children of `<body>`."""

REPLACE: Final = "replace"
"""The flag of a `context` op that replaces the page-owned keys instead of merging."""

LAYOUTS: Final = "layouts"
"""Navigation field, `[id, digest, seal]` per layout, outer first, null seal if kept."""

ROOT: Final = "root"
"""Navigation field, the id of the outermost layout of the page, null for none."""

NONCE: Final = "nonce"
"""Navigation field, the CSP nonce of the response, null without one."""

SCRIPTS: Final = "scripts"
"""Navigation field, the `$scripts` manifest entries of the page."""

CONSENT: Final = "consent"
"""Navigation field, the `$consent` state the page renders under."""

SHARED: Final = "shared"
"""Navigation field, true when a shared cache may store the document of the page."""

COMPLETE: Final = "complete"
"""Prefetch field, true when the answer skipped no deferred job, absent otherwise."""

NAVIGATION_KEYS: Final[tuple[str, ...]] = (
    LAYOUTS,
    ROOT,
    NONCE,
    SCRIPTS,
    CONSENT,
    SHARED,
    COMPLETE,
)
"""The top-level fields a navigation envelope adds, in wire order."""
EVENT_NAME: Final = "name"
FORM_SELECTOR: Final = "form"

FORM_ZONE_ATTR: Final = "data-next-target"
FORM_KEY_ATTR: Final = "data-next-key"

RESERVED_PATCH_KEYS: Final[frozenset[str]] = frozenset({OP, TARGET, HTML})

CHUNK_BOUNDARY: Final = "z"
CHUNK_STATE: Final = "s"
CHUNK_KIND: Final = "k"
CHUNK_PARENT: Final = "p"
CHUNK_ENVELOPE: Final = "e"
CHUNK_DEBUG: Final = "d"
CHUNK_DIGEST: Final = "g"
CHUNK_END: Final = 0


__all__ = [
    "ASSETS",
    "BODY",
    "BOUNDARY",
    "CHUNK_BOUNDARY",
    "CHUNK_DEBUG",
    "CHUNK_DIGEST",
    "CHUNK_END",
    "CHUNK_ENVELOPE",
    "CHUNK_KIND",
    "CHUNK_PARENT",
    "CHUNK_STATE",
    "COMPLETE",
    "CONSENT",
    "CSRF",
    "EPOCH",
    "ERRORS",
    "EVENT_NAME",
    "FORM",
    "FORM_KEY_ATTR",
    "FORM_SELECTOR",
    "FORM_ZONE_ATTR",
    "HTML",
    "INLINE",
    "KIND",
    "LAYOUTS",
    "LOAD",
    "NAVIGATION_KEYS",
    "NONCE",
    "OP",
    "OPS",
    "PAGE_SCOPE",
    "REPLACE",
    "REQUEST_ID",
    "RESERVED_PATCH_KEYS",
    "ROOT",
    "SCOPE",
    "SCRIPTS",
    "SEQ",
    "SERVER_TIME",
    "SHARED",
    "SLOT",
    "SSE_END",
    "SSE_HELLO",
    "SSE_SKIP",
    "TARGET",
    "TOPIC",
    "UID",
    "URL",
    "VALID",
    "VERSION",
    "ZONE",
]
