"""System checks for next-dj template engine wiring.

Registered identifiers.

- `next.W062`. Warning raised when no `DjangoTemplates` engine is
  configured in `TEMPLATES`. The next-dj `{% %}` tags install only into
  that backend, so they are unavailable without it.
- `next.W063`. Warning raised when a tag library module under
  `next.templatetags` is not listed in the explicit builtin tuple, so it
  never installs as a template builtin.
"""

from __future__ import annotations

import pkgutil
from importlib import import_module

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template import Library

import next.templatetags

from .templates import _BUILTIN_MODULES, _DJANGO_BACKEND


@register(Tags.templates)
def check_django_templates_backend_present(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when no DjangoTemplates engine carries the next-dj tags."""
    engines = getattr(settings, "TEMPLATES", [])
    if any(engine.get("BACKEND") == _DJANGO_BACKEND for engine in engines):
        return []
    return [
        DjangoWarning(
            "next-dj template tags require a DjangoTemplates backend. "
            "None is configured, so the {% %} tags will be unavailable.",
            obj=settings,
            id="next.W062",
        ),
    ]


def _iter_tag_library_modules() -> list[str]:
    """Return the dotted names of tag library modules under next.templatetags.

    A module counts as a tag library when it exposes a `register` attribute
    that is a Django `Library`, the same shape `get_installed_libraries`
    loads. This walks the package only to discover the modules that exist
    on disk, the builtin registration list itself stays the explicit
    `_BUILTIN_MODULES` tuple.
    """
    found: list[str] = []
    package_path = next.templatetags.__path__
    package_name = next.templatetags.__name__
    for module_info in pkgutil.iter_modules(package_path):
        if module_info.ispkg:
            continue
        dotted = f"{package_name}.{module_info.name}"
        module = import_module(dotted)
        if isinstance(getattr(module, "register", None), Library):
            found.append(dotted)
    return found


@register(Tags.templates)
def check_builtin_tag_libraries_complete(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a tag library is not registered as a builtin (`next.W063`).

    The builtin registration list is the explicit `_BUILTIN_MODULES` tuple.
    A tag library module added under `next.templatetags` but left out of
    that tuple installs into no engine, so its tags silently fail to load.
    This check pairs the explicit list with a completeness probe over the
    modules that exist on disk.
    """
    builtins = set(_BUILTIN_MODULES)
    return [
        DjangoWarning(
            f"Tag library {dotted!r} exposes a Library but is not listed in "
            "next.apps.templates._BUILTIN_MODULES, so it never installs as a "
            "template builtin. Add it to the tuple to register its tags.",
            obj=dotted,
            id="next.W063",
        )
        for dotted in _iter_tag_library_modules()
        if dotted not in builtins
    ]


__all__ = [
    "check_builtin_tag_libraries_complete",
    "check_django_templates_backend_present",
]
