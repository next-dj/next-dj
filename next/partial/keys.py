"""Wire-key constants of the partial protocol, shared by every area that writes them.

Frozen wire contract mirrored in next/client/protocol.ts and apply.ts.
"""

from typing import Final

from next.wire import (
    ASSET_ATTR,
    BOUNDARY_ATTR,
    BOUNDARY_KEY_ATTR,
    CURRENT_ATTR,
    DETAIL_ATTR,
    DIGEST_ATTR,
    ERROR_ATTR,
    KEY_ATTR,
    SOURCE_ATTR,
    STATE_ATTR,
)


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
SSE_PATCHES: Final = "next-patches"
"""The SSE event name of an envelope a realtime stream carries."""

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
"""Navigation field, `[id, digest, response id]` per layout, outer first."""

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

DROPPED: Final = "dropped"
"""Navigation field under `DEBUG`, the script that sent the client to load the page."""

NAVIGATION_KEYS: Final[tuple[str, ...]] = (
    LAYOUTS,
    ROOT,
    NONCE,
    SCRIPTS,
    CONSENT,
    SHARED,
    COMPLETE,
    DROPPED,
)
"""The top-level fields a navigation envelope adds, in wire order."""
EVENT_NAME: Final = "name"
FORM_SELECTOR: Final = "form"

TARGET_ATTR: Final = "data-next-target"
"""The attribute naming the zone a form or a more control swaps."""

NAV_ATTR: Final = "data-next-nav"
"""The author mark of a container whose anchors the active-link pass reads."""

MATCH_ATTR: Final = "data-next-match"
"""The author mark choosing how an anchor matches the current path."""

FOCUS_ATTR: Final = "data-next-focus"
"""The author mark of the element a swap hands the focus to."""

PERSIST_ATTR: Final = "data-next-persist"
"""The author mark of an element a swap carries over unchanged."""

PREFETCH_ATTR: Final = "data-next-prefetch"
"""The author mark tuning or refusing the prefetch of an anchor."""

PRESS_ATTR: Final = "data-next-press"
"""The author mark of an anchor whose press starts the request."""

ROUTER_ATTR: Final = "data-next-router"
"""The author mark opting an element out of the client router."""

SCROLL_ATTR: Final = "data-next-scroll"
"""The author mark of the scroll a swap restores."""

SCROLL_REGION_ATTR: Final = "data-next-scroll-region"
"""The author mark of a scrollable region the router keeps in place."""

TOASTS_ATTR: Final = "data-next-toasts"
"""The author mark of the container toasts land in."""

RESERVE_ATTR: Final = "data-next-reserve"
"""The author mark of a zone that keeps its height while it reloads."""

FETCH_ATTR: Final = "data-next-fetch"
"""The author mark of an anchor the router loads without navigating."""

CONFIRM_ATTR: Final = "data-next-confirm"
"""The author mark of an anchor that asks before it navigates."""

NOSCRIPT_ATTR: Final = "data-next-noscript"
"""The mark of the link a reader without scripts follows to the whole page."""

CONSENTED_ATTR: Final = "data-next-consented"
"""The mark of a template holding what a script waits for consent to run."""

VALIDATE_ATTR: Final = "data-next-validate"
"""The form attribute naming the fields validated as they change."""

TRIGGER_ATTR: Final = "data-next-trigger"
"""The form attribute naming the event that submits it for validation."""

DEBOUNCE_ATTR: Final = "data-next-debounce"
"""The form attribute holding the wait before a validation request."""

TRIP_ATTR: Final = "data-next-token"
"""The attribute holding the token a client-trip or retry wrapper fetches with."""

CARRIER_ATTR: Final = "data-next-carrier"
"""The `place:name` a client-trip wrapper sends its token in, other than the header."""

ADOPTED_ATTR: Final = "data-next-adopted"
"""The mark of a wrapper the root of its content replaces as it settles."""

STREAMED_ATTR: Final = "data-next-streamed"
"""The mark of the root element of a document whose boundaries stream in."""

RESERVED_PATCH_KEYS: Final[frozenset[str]] = frozenset({OP, TARGET, HTML})

CHUNK_BOUNDARY: Final = "z"
CHUNK_STATE: Final = "s"
CHUNK_KIND: Final = "k"
CHUNK_PARENT: Final = "p"
CHUNK_ENVELOPE: Final = "e"
CHUNK_DEBUG: Final = "d"
CHUNK_DIGEST: Final = "g"
CHUNK_TEMPLATE: Final = "t"
"""The id of the `<template>` holding the HTML of a document chunk's boundary op."""
CHUNK_END: Final = 0

NOSCRIPT_PARAM: Final = "_nojs"
"""The query flag of a page asked for without scripts, every boundary in place."""


__all__ = [
    "ADOPTED_ATTR",
    "ASSETS",
    "ASSET_ATTR",
    "BODY",
    "BOUNDARY",
    "BOUNDARY_ATTR",
    "BOUNDARY_KEY_ATTR",
    "CARRIER_ATTR",
    "CHUNK_BOUNDARY",
    "CHUNK_DEBUG",
    "CHUNK_DIGEST",
    "CHUNK_END",
    "CHUNK_ENVELOPE",
    "CHUNK_KIND",
    "CHUNK_PARENT",
    "CHUNK_STATE",
    "CHUNK_TEMPLATE",
    "COMPLETE",
    "CONFIRM_ATTR",
    "CONSENT",
    "CONSENTED_ATTR",
    "CSRF",
    "CURRENT_ATTR",
    "DEBOUNCE_ATTR",
    "DETAIL_ATTR",
    "DIGEST_ATTR",
    "DROPPED",
    "EPOCH",
    "ERRORS",
    "ERROR_ATTR",
    "EVENT_NAME",
    "FETCH_ATTR",
    "FOCUS_ATTR",
    "FORM",
    "FORM_SELECTOR",
    "HTML",
    "INLINE",
    "KEY_ATTR",
    "KIND",
    "LAYOUTS",
    "LOAD",
    "MATCH_ATTR",
    "NAVIGATION_KEYS",
    "NAV_ATTR",
    "NONCE",
    "NOSCRIPT_ATTR",
    "NOSCRIPT_PARAM",
    "OP",
    "OPS",
    "PAGE_SCOPE",
    "PERSIST_ATTR",
    "PREFETCH_ATTR",
    "PRESS_ATTR",
    "REPLACE",
    "REQUEST_ID",
    "RESERVED_PATCH_KEYS",
    "RESERVE_ATTR",
    "ROOT",
    "ROUTER_ATTR",
    "SCOPE",
    "SCRIPTS",
    "SCROLL_ATTR",
    "SCROLL_REGION_ATTR",
    "SEQ",
    "SERVER_TIME",
    "SHARED",
    "SLOT",
    "SOURCE_ATTR",
    "SSE_END",
    "SSE_HELLO",
    "SSE_PATCHES",
    "SSE_SKIP",
    "STATE_ATTR",
    "STREAMED_ATTR",
    "TARGET",
    "TARGET_ATTR",
    "TOASTS_ATTR",
    "TOPIC",
    "TRIGGER_ATTR",
    "TRIP_ATTR",
    "UID",
    "URL",
    "VALID",
    "VALIDATE_ATTR",
    "VERSION",
    "ZONE",
]
