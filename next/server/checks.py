"""System checks for the development server `manage.py runserver` runs."""

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.core.exceptions import ImproperlyConfigured
from django.core.management import get_commands

from next.checks import NEXT
from next.conf import next_framework_settings
from next.conf.defaults import DEFAULTS

from .asgi import DEV_SERVER_KEY, server_class, server_installed


_OWN_APP = "next"


@register(NEXT)
def check_dev_server(*args, **kwargs) -> list[CheckMessage]:
    """Report a `DEV_SERVER` path that names no `ASGIServer` class (`next.E212`)."""
    path = getattr(next_framework_settings, DEV_SERVER_KEY)
    if path == DEFAULTS[DEV_SERVER_KEY]:
        return []
    try:
        server_class(path)
    except ImproperlyConfigured as exc:
        return [
            Error(
                str(exc),
                hint="Name a class whose run(app, **options) serves an ASGI app.",
                obj=settings,
                id="next.E212",
            )
        ]
    return []


@register(NEXT)
def check_runserver_override_order(*args, **kwargs) -> list[CheckMessage]:
    """Warn when an app above next ships the `runserver` that runs (`next.W148`).

    Django runs the command of the app listed first, so the override silently drops.
    """
    if not server_installed():
        return []
    owner = get_commands()["runserver"]
    if owner == _OWN_APP:
        return []
    return [
        DjangoWarning(
            f"{owner} is listed above next in INSTALLED_APPS and ships its own "
            "runserver, so manage.py runserver is that command and never serves the "
            "project on the ASGI server with the realtime WebSocket.",
            hint=f"Move 'next' above '{owner}' in INSTALLED_APPS.",
            obj=settings,
            id="next.W148",
        )
    ]


__all__ = ["check_dev_server", "check_runserver_override_order"]
