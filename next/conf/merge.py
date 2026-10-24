"""One-level merge of the user `NEXT_FRAMEWORK` mapping over framework defaults.

A user value replaces the default whole, and a mistyped one is dropped and checked.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from .defaults import DEFAULTS
from .frozen import freeze
from .sentinels import UNSET


if TYPE_CHECKING:
    from collections.abc import Mapping


LIST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "PAGE_BACKENDS",
        "COMPONENT_BACKENDS",
        "STATIC_BACKENDS",
        "FORM_ACTION_BACKENDS",
        "PARTIAL_BACKENDS",
        "TEMPLATE_LOADERS",
        "FORM_ANCHOR_FILES",
    }
)
DICT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "NEXT_JS_OPTIONS",
        "FORM_WIZARD_BACKEND",
        "METADATA",
        "REALTIME",
        "SITE",
        "SEO",
        "CONSENT",
    }
)
STR_KEYS: Final[frozenset[str]] = frozenset(
    {
        "COMPONENT_TEMPLATE_LOADER",
        "CSRF_DELIVERY",
        "DEPENDENCY_RESOLVER",
        "URL_NAME_TEMPLATE",
        "URL_RESOLVER",
    }
)
OPTIONAL_STR_KEYS: Final[frozenset[str]] = frozenset(
    {"JS_CONTEXT_SERIALIZER", "STATIC_VERSION"}
)
BOOL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "STRICT_CONTEXT",
        "STRICT_LOADING",
        "LOGIN_REQUIRED",
        "LAZY_COMPONENT_MODULES",
        "FORM_AUTODISCOVER",
        "STATIC_DISCOVERY_CACHE",
        "CSP_NONCE",
    }
)


FROZEN_DEFAULTS: Final[Mapping[str, Any]] = MappingProxyType(
    {key: freeze(value) for key, value in DEFAULTS.items()}
)
"""`DEFAULTS` frozen once on import, as the framework never mutates its defaults."""


def accepted_value(key: str, raw: object) -> object:
    """Return what one user value merges to, or `UNSET` when its type is unusable."""
    if key in STR_KEYS:
        return raw if isinstance(raw, str) else UNSET
    if key in LIST_KEYS:
        return freeze(raw) if isinstance(raw, list) else UNSET
    if key in DICT_KEYS:
        return freeze(raw) if isinstance(raw, dict) else UNSET
    if key in BOOL_KEYS:
        return bool(raw)
    if key in OPTIONAL_STR_KEYS and (raw is None or isinstance(raw, str)):
        return raw
    return UNSET


def merge_user_settings(
    defaults: Mapping[str, Any], user: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return the frozen defaults with every usable user value laid over them.

    The framework `DEFAULTS` reuse their import-time freeze, other mappings freeze here.
    """
    merged: dict[str, Any] = (
        dict(FROZEN_DEFAULTS)
        if defaults is DEFAULTS
        else {key: freeze(value) for key, value in defaults.items()}
    )
    if not user:
        return merged
    for key in defaults:
        if key not in user:
            continue
        value = accepted_value(key, user[key])
        if value is not UNSET:
            merged[key] = value
    return merged


__all__ = [
    "BOOL_KEYS",
    "DICT_KEYS",
    "FROZEN_DEFAULTS",
    "LIST_KEYS",
    "OPTIONAL_STR_KEYS",
    "STR_KEYS",
    "UNSET",
    "accepted_value",
    "merge_user_settings",
]
