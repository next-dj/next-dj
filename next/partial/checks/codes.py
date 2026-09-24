"""Check identifiers the partial-rendering checks emit.

The ids sit in one module so a submodule names a code without importing a sibling.
"""

from typing import Final


E_DUPLICATE_ZONE: Final = "next.E060"
E_NON_ASCII_ZONE: Final = "next.E061"
E_ZONE_IN_FOR: Final = "next.E062"
E_ZONE_IN_IF: Final = "next.E063"
E_LAZY_WITHOUT_PLACEHOLDER: Final = "next.E064"
E_ZONE_IN_COMPONENT: Final = "next.E065"
E_OP_SHADOWS_BUILTIN: Final = "next.E066"
E_BACKENDS_NOT_A_LIST: Final = "next.E067"
E_COMPOSED_TEMPLATE_SYNTAX: Final = "next.E072"
E_BACKEND_WITHOUT_PATH: Final = "next.E073"
E_CONTEXT_ZONE_UNKNOWN: Final = "next.E078"
E_OP_BAD_NAME: Final = "next.E090"

W_WITH_OVER_ZONE: Final = "next.W067"
W_FORM_BACKEND_NOT_AWARE: Final = "next.W068"
W_MANIFEST_VERSION_NO_STORAGE: Final = "next.W069"
W_FORM_IN_FOR_NO_KEY: Final = "next.W070"
W_TOO_MANY_BACKENDS: Final = "next.W071"
W_ASSET_VERSION_FROZEN: Final = "next.W083"

__all__ = [
    "E_BACKENDS_NOT_A_LIST",
    "E_BACKEND_WITHOUT_PATH",
    "E_COMPOSED_TEMPLATE_SYNTAX",
    "E_CONTEXT_ZONE_UNKNOWN",
    "E_DUPLICATE_ZONE",
    "E_LAZY_WITHOUT_PLACEHOLDER",
    "E_NON_ASCII_ZONE",
    "E_OP_BAD_NAME",
    "E_OP_SHADOWS_BUILTIN",
    "E_ZONE_IN_COMPONENT",
    "E_ZONE_IN_FOR",
    "E_ZONE_IN_IF",
    "W_ASSET_VERSION_FROZEN",
    "W_FORM_BACKEND_NOT_AWARE",
    "W_FORM_IN_FOR_NO_KEY",
    "W_MANIFEST_VERSION_NO_STORAGE",
    "W_TOO_MANY_BACKENDS",
    "W_WITH_OVER_ZONE",
]
