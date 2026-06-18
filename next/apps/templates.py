"""Register next-dj templatetag modules as Django template builtins."""

from __future__ import annotations

from django.conf import settings


_BUILTIN_MODULES = (
    "next.templatetags.forms",
    "next.templatetags.components",
    "next.templatetags.next_static",
    "next.templatetags.partial",
)

_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"


def install() -> None:
    """Add next-dj templatetag modules to every DjangoTemplates engine."""
    for engine in settings.TEMPLATES:
        if engine.get("BACKEND") != _DJANGO_BACKEND:
            continue
        options = engine.setdefault("OPTIONS", {})
        builtins = list(options.get("builtins", []))
        for module in _BUILTIN_MODULES:
            if module not in builtins:
                builtins.append(module)
        options["builtins"] = builtins


__all__ = ["install"]
