"""`manage.py runserver`, on uvicorn with the realtime WebSocket once it is installed.

Django runs the command of the app listed first, so `next` sits above staticfiles.
"""

import os
import sys
from typing import Any, override

from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.handlers.wsgi import WSGIHandler
from django.core.management.base import CommandParser
from django.core.management.commands.runserver import Command as RunserverCommand
from django.db import connections
from django.utils import autoreload

from next.server.asgi import (
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


class Command(RunserverCommand):
    """Serve the project on uvicorn when it is installed, else as Django's runserver.

    The static options join only with staticfiles installed, as in Django.
    """

    addr: str
    port: str
    _raw_ipv6: bool

    help = (
        "Start a development server, on uvicorn with the realtime WebSocket when "
        "uvicorn is installed."
    )

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        """Take Django's arguments and, with staticfiles installed, its static ones."""
        super().add_arguments(parser)
        if not apps.is_installed(_STATICFILES):
            return
        parser.add_argument(
            "--nostatic",
            action="store_false",
            dest="use_static_handler",
            help="Do not serve static files at STATIC_URL.",
        )
        parser.add_argument(
            "--insecure",
            action="store_true",
            dest="insecure_serving",
            help="Serve static files even if DEBUG is False.",
        )

    @override
    def get_handler(self, *args: Any, **options: Any) -> WSGIHandler:
        """Return the WSGI handler, serving static files the way staticfiles does."""
        handler: WSGIHandler = super().get_handler(*args, **options)
        if _serves_static(options):
            return StaticFilesHandler(handler)
        return handler

    @override
    def inner_run(self, *args: Any, **options: Any) -> None:
        """Check the project as Django does, then serve it on uvicorn when installed."""
        server = asgi_server()
        if server is None:
            super().inner_run(*args, **options)
            return
        autoreload.raise_last_exception()
        if not options["skip_checks"]:
            self.stdout.write("Performing system checks...\n\n")
            self.check(display_num_errors=True)
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


def _serves_static(options: dict[str, Any]) -> bool:
    """Whether staticfiles is installed and static serving is on, as in Django."""
    if not options.get("use_static_handler", False):
        return False
    return bool(settings.DEBUG or options["insecure_serving"])
