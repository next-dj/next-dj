"""System checks for the partial-rendering subsystem.

This module is excluded from coverage like every other area `checks.py`.
It pins the check-id registry so each id is reserved in one place as the
zone, form, and asset checks are wired onto it.
"""

from typing import Final


E_DUPLICATE_ZONE: Final = "next.E060"
E_NON_ASCII_ZONE: Final = "next.E061"
E_ZONE_IN_FOR: Final = "next.E062"
E_ZONE_IN_IF: Final = "next.E063"
E_LAZY_WITHOUT_PLACEHOLDER: Final = "next.E064"
E_ZONE_IN_COMPONENT: Final = "next.E065"
E_UNREGISTERED_OP: Final = "next.E066"

W_WITH_OVER_ZONE: Final = "next.W067"
W_FORM_BACKEND_NOT_AWARE: Final = "next.W068"
W_MANIFEST_VERSION_NO_STORAGE: Final = "next.W069"


CHECK_IDS: Final = (
    E_DUPLICATE_ZONE,
    E_NON_ASCII_ZONE,
    E_ZONE_IN_FOR,
    E_ZONE_IN_IF,
    E_LAZY_WITHOUT_PLACEHOLDER,
    E_ZONE_IN_COMPONENT,
    E_UNREGISTERED_OP,
    W_WITH_OVER_ZONE,
    W_FORM_BACKEND_NOT_AWARE,
    W_MANIFEST_VERSION_NO_STORAGE,
)


__all__ = [
    "CHECK_IDS",
    "E_DUPLICATE_ZONE",
    "E_LAZY_WITHOUT_PLACEHOLDER",
    "E_NON_ASCII_ZONE",
    "E_UNREGISTERED_OP",
    "E_ZONE_IN_COMPONENT",
    "E_ZONE_IN_FOR",
    "E_ZONE_IN_IF",
    "W_FORM_BACKEND_NOT_AWARE",
    "W_MANIFEST_VERSION_NO_STORAGE",
    "W_WITH_OVER_ZONE",
]
