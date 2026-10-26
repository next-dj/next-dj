"""System checks for next-dj template engine wiring and app order."""

from __future__ import annotations

import pkgutil
from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register
from django.template import Library

import next.templatetags
from next.checks import NEXT
from next.server.asgi import server_installed

from .templates import _BUILTIN_MODULES, _DJANGO_BACKEND


@register(Tags.templates, NEXT)
def check_django_templates_backend_present(*args, **kwargs) -> list[CheckMessage]:
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
        )
    ]


def _iter_tag_library_modules() -> list[str]:
    """Return the dotted names of tag library modules under next.templatetags.

    A module counts when its `register` attribute is a Django `Library`.
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


@register(Tags.templates, NEXT)
def check_builtin_tag_libraries_complete(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a tag library is not registered as a builtin (`next.W063`).

    A module left out of `_BUILTIN_MODULES` installs into no engine, so its tags fail.
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


_STATICFILES = "django.contrib.staticfiles"


@register(Tags.staticfiles, NEXT)
def check_runserver_override_order(*args, **kwargs) -> list[CheckMessage]:
    """Warn when staticfiles shadows the ASGI `runserver` of next (`next.W148`).

    Django runs the command of the app listed first, so the override silently drops.
    """
    if not server_installed():
        return []
    names = [config.name for config in apps.get_app_configs()]
    if _STATICFILES not in names or names.index(_STATICFILES) > names.index("next"):
        return []
    return [
        DjangoWarning(
            f"{_STATICFILES} is listed above next in INSTALLED_APPS, so manage.py "
            "runserver is the staticfiles command and never serves the project on "
            "uvicorn with the realtime WebSocket.",
            hint=f"Move 'next' above '{_STATICFILES}' in INSTALLED_APPS.",
            obj=settings,
            id="next.W148",
        )
    ]


__all__ = [
    "check_builtin_tag_libraries_complete",
    "check_django_templates_backend_present",
    "check_runserver_override_order",
]
