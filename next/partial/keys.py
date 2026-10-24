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

UID: Final = "uid"
VALID: Final = "valid"
ERRORS: Final = "errors"

ZONE: Final = "zone"
FORM_SELECTOR: Final = "form"

FORM_ZONE_ATTR: Final = "data-next-target"
FORM_KEY_ATTR: Final = "data-next-key"

RESERVED_PATCH_KEYS: Final[frozenset[str]] = frozenset({OP, TARGET, HTML})


__all__ = [
    "ASSETS",
    "CSRF",
    "EPOCH",
    "ERRORS",
    "FORM",
    "FORM_KEY_ATTR",
    "FORM_SELECTOR",
    "FORM_ZONE_ATTR",
    "HTML",
    "INLINE",
    "KIND",
    "LOAD",
    "OP",
    "OPS",
    "REQUEST_ID",
    "RESERVED_PATCH_KEYS",
    "SEQ",
    "SERVER_TIME",
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
