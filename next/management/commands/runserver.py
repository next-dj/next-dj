"""`manage.py runserver`, over ASGI with the realtime WebSocket once a server imports.

Django runs the command of the app listed first, so `next` sits above staticfiles.
"""

import os
import sys
from typing import Any, override

from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.wsgi import WSGIHandler
from django.core.management.base import CommandError, CommandParser
from django.core.management.commands.runserver import Command as RunserverCommand
from django.db import connections
from django.utils import autoreload

from next.server.asgi import (
    ASGIServer,
    asgi_server,
    banner,
    development_warning,
    display_host,
    offers_websocket,
    serve_development,
    speaks_websocket,
)


_STATICFILES = "django.contrib.staticfiles"
_NO_WEBSOCKET = (
    "uvicorn found no WebSocket library, so pages fall back to SSE. "
    "Install websockets or wsproto."
)


class Command(StaticfilesRunserverCommand):
    """Serve the project on the `DEV_SERVER` when it is installed, else over WSGI.

    The static options and handler of staticfiles apply while that app is installed.
    """

    addr: str
    port: str
    _raw_ipv6: bool

    help = (
        "Start a development server, over ASGI with the realtime WebSocket when "
        "the configured server is installed."
    )

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        """Take Django's arguments, the static ones of staticfiles, and `--wsgi`."""
        if apps.is_installed(_STATICFILES):
            super().add_arguments(parser)
        else:
            RunserverCommand.add_arguments(self, parser)
        parser.add_argument(
            "--wsgi",
            action="store_true",
            dest="use_wsgi",
            help="Serve Django's WSGI handler even when the ASGI server is installed.",
        )

    @override
    def get_handler(self, *args: Any, **options: Any) -> WSGIHandler:
        """Wrap the WSGI handler for static files as staticfiles does, if installed."""
        if apps.is_installed(_STATICFILES):
            return super().get_handler(*args, **options)
        return RunserverCommand.get_handler(self, *args, **options)

    @override
    def inner_run(self, *args: Any, **options: Any) -> None:
        """Check the project as Django does, then serve it over ASGI when it can.

        The checks run before the server resolves, so a bad `DEV_SERVER` is `E212`.
        """
        if options.get("use_wsgi"):
            super().inner_run(*args, **options)
            return
        autoreload.raise_last_exception()
        if not options["skip_checks"]:
            self.stdout.write("Performing system checks...\n\n")
            self.check(display_num_errors=True)
        server = _configured_server()
        if server is None:
            super().inner_run(*args, **{**options, "skip_checks": True})
            return
        self.check_migrations()
        for connection in connections.all(initialized_only=True):
            connection.close()
        try:
            serve_development(
                server,
                host=self.addr,
                port=int(self.port),
                static=_serves_static(options),
                announce=self._announce,
            )
        except SystemExit:
            os._exit(1)
        except KeyboardInterrupt:
            if shutdown_message := options.get("shutdown_message", ""):
                self.stdout.write(shutdown_message)
            sys.exit(0)

    def _announce(self) -> None:
        """Print the banner of Django's server, naming ASGI and a mounted socket."""
        offered = offers_websocket()
        speaks = speaks_websocket()
        host = display_host(self.addr, ipv6=self._raw_ipv6)
        url = f"{self.protocol}://{host}:{self.port}/"
        self.stdout.write(banner(url, websocket=offered and speaks))
        if warning := development_warning():
            self.stdout.write(self.style.WARNING(warning))
        if offered and not speaks:
            self.stderr.write(_NO_WEBSOCKET)


def _configured_server() -> ASGIServer | None:
    """Return the development server, a bad `DEV_SERVER` as the error naming `E212`."""
    try:
        return asgi_server()
    except ImproperlyConfigured as exc:
        msg = f"{exc} (next.E212)"
        raise CommandError(msg) from exc


def _serves_static(options: dict[str, Any]) -> bool:
    """Whether staticfiles serves the static files, the way its command decides."""
    if not options.get("use_static_handler", False):
        return False
    return bool(settings.DEBUG or options.get("insecure_serving", False))
