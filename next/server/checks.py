"""System checks for the development server `manage.py runserver` runs."""

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register
from django.core.exceptions import ImproperlyConfigured

from next.checks import NEXT
from next.conf import next_framework_settings
from next.conf.defaults import DEFAULTS

from .asgi import DEV_SERVER_KEY, server_class


@register(Tags.staticfiles, NEXT)
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


__all__ = ["check_dev_server"]
