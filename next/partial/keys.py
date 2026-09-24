"""Wire-key constants of the partial protocol, shared by every area that writes them.

These names are the frozen partial wire contract. The TypeScript runtime mirrors
them in next/client/protocol.ts and the per-op fields in next/client/apply.ts, so
a change here is a wire break that must move in lockstep with the client.
"""

from typing import Final


VERSION: Final = "version"
OPS: Final = "ops"
ASSETS: Final = "assets"
FORM: Final = "form"
CSRF: Final = "csrf"
REQUEST_ID: Final = "request_id"

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
    "TARGET",
    "UID",
    "URL",
    "VALID",
    "VERSION",
    "ZONE",
]
