"""The navigation of the client router, rendered as a document, answered by change."""

from .clearing import (
    CLEAR,
    NO_SNAPSHOT,
    clear_on_login,
    clear_on_logout,
    directives,
    router_clear,
    stamp,
)
from .cuts import Foreseen, cleared, foresee
from .envelopes import answered, navigation_envelope, passed, redirects, visit_envelope
from .intent import DECLARATION_BYTES, declaration, hostlike, parse_declaration
from .interception import intercepted
from .marking import (
    Compiler,
    action_marking,
    fragment_marking,
    marked_links,
    origin_vary,
    page_marking,
    zone_marking,
)
from .reporting import announce, debug_cut, describe
from .shedding import refused_prefetch, shed, stale
from .shell import layouts_payload, read_shell
from .state import Routing, routing


__all__ = [
    "CLEAR",
    "DECLARATION_BYTES",
    "NO_SNAPSHOT",
    "Compiler",
    "Foreseen",
    "Routing",
    "action_marking",
    "announce",
    "answered",
    "clear_on_login",
    "clear_on_logout",
    "cleared",
    "debug_cut",
    "declaration",
    "describe",
    "directives",
    "foresee",
    "fragment_marking",
    "hostlike",
    "intercepted",
    "layouts_payload",
    "marked_links",
    "navigation_envelope",
    "origin_vary",
    "page_marking",
    "parse_declaration",
    "passed",
    "read_shell",
    "redirects",
    "refused_prefetch",
    "router_clear",
    "routing",
    "shed",
    "stale",
    "stamp",
    "visit_envelope",
    "zone_marking",
]
