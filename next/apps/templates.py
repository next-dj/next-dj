"""Register next-dj templatetag modules as Django template builtins."""

from __future__ import annotations

from django.conf import settings


_BUILTIN_MODULES = (
    "next.templatetags.forms",
    "next.templatetags.components",
    "next.templatetags.next_static",
)


def install() -> None:
    """Add next-dj templatetag modules to `TEMPLATES[0].OPTIONS.builtins`."""
    builtins = list(settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", []))
    for module in _BUILTIN_MODULES:
        if module not in builtins:
            builtins.append(module)
    settings.TEMPLATES[0].setdefault("OPTIONS", {})["builtins"] = builtins


__all__ = ["install"]
