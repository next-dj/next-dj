"""The ASGI app and server behind `manage.py runserver` once its server is installed.

The command only maps its options onto these, so a framework CLI can serve the same app.
"""

import importlib
import importlib.util
import os
import socket
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

from asgiref.typing import ASGI3Application
from django import get_version
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from django.utils.version import get_docs_version

from next.conf import next_framework_settings
from next.conf.defaults import DEFAULTS
from next.realtime import application
from next.realtime.frames import MAX_INBOUND_BYTES
from next.realtime.transports import active_transports


if TYPE_CHECKING:
    from django.core.handlers.asgi import ASGIHandler


_WEBSOCKET_LIBRARIES = ("websockets", "wsproto")
_HIDE_WARNING = "DJANGO_RUNSERVER_HIDE_WARNING"


DEV_SERVER_KEY: Final = "DEV_SERVER"


@runtime_checkable
class ASGIServer(Protocol):
    """A server that runs an ASGI app until it stops, the way `uvicorn.run` does."""

    def run(self, app: ASGI3Application, **options: object) -> None:
        """Serve `app` with the server's own options."""


class UvicornServer:
    """Runs the app on uvicorn, the development server `DEV_SERVER` names by default."""

    def run(self, app: ASGI3Application, **options: object) -> None:
        """Serve `app` through `uvicorn.run`, imported here as uvicorn is optional."""
        importlib.import_module("uvicorn").run(app, **options)


def server_class(path: str) -> type[ASGIServer]:
    """Return the `ASGIServer` class a dotted path names, raising for any other."""
    try:
        klass = import_string(path)
    except ImportError as exc:
        msg = f"NEXT_FRAMEWORK['{DEV_SERVER_KEY}'] {path!r} does not import. {exc}"
        raise ImproperlyConfigured(msg) from exc
    if not isinstance(klass, type) or not issubclass(klass, ASGIServer):
        msg = (
            f"NEXT_FRAMEWORK['{DEV_SERVER_KEY}'] {path!r} is no class with the "
            "run(app, **options) method of ASGIServer."
        )
        raise ImproperlyConfigured(msg)
    return klass


def server_installed() -> bool:
    """Whether `DEV_SERVER` names a server, false for the default without uvicorn."""
    path = getattr(next_framework_settings, DEV_SERVER_KEY)
    return path != DEFAULTS[DEV_SERVER_KEY] or bool(importlib.util.find_spec("uvicorn"))


def asgi_server() -> ASGIServer | None:
    """Return the configured development server, None when the default lacks uvicorn.

    A project that names its own server gets it built, or the error of its path.
    """
    if not server_installed():
        return None
    return server_class(getattr(next_framework_settings, DEV_SERVER_KEY))()


def speaks_websocket() -> bool:
    """Whether uvicorn finds a library to accept a WebSocket upgrade with."""
    return any(importlib.util.find_spec(name) for name in _WEBSOCKET_LIBRARIES)


def offers_websocket() -> bool:
    """Whether pages offer the realtime WebSocket, mounted by the app built here."""
    return "ws" in active_transports()


def project_application() -> ASGI3Application:
    """Return the app `ASGI_APPLICATION` names, else Django's under the realtime socket.

    The setting is the one Daphne and Channels read, and Django itself has no such key.
    """
    path = getattr(settings, "ASGI_APPLICATION", None)
    if not path:
        return application(cast("ASGI3Application", get_asgi_application()))
    try:
        app: ASGI3Application = import_string(path)
    except ImportError as exc:
        msg = f"ASGI application {path!r} could not be loaded. {exc}"
        raise ImproperlyConfigured(msg) from exc
    return app


def development_application(*, static: bool) -> ASGI3Application:
    """Return the project app, with `STATIC_URL` served from the finders when asked.

    The casts bridge django-stubs, which types every ASGI app as an `ASGIHandler`.
    """
    app = project_application()
    if not static:
        return app
    return cast("ASGI3Application", ASGIStaticFilesHandler(cast("ASGIHandler", app)))


def server_options() -> dict[str, object]:
    """Return the uvicorn options the realtime socket needs over uvicorn's defaults.

    The socket refuses a larger frame anyway and drops a peer that stops answering.
    """
    return {"ws_max_size": MAX_INBOUND_BYTES, "ws_ping_interval": None}


def serve(
    server: ASGIServer,
    app: ASGI3Application,
    *,
    host: str,
    port: int,
    options: Mapping[str, object] | None = None,
) -> None:
    """Run `app` on the server until it stops, leaving restarts to Django's reloader."""
    server.run(app, host=host, port=port, **{**server_options(), **(options or {})})


def serve_development(
    server: ASGIServer,
    *,
    host: str,
    port: int,
    static: bool,
    announce: Callable[[], None] | None = None,
    options: Mapping[str, object] | None = None,
) -> None:
    """Build the development app, call `announce` once it is mounted, then serve it."""
    app = development_application(static=static)
    if announce is not None:
        announce()
    serve(server, app, host=host, port=port, options=options)


def display_host(host: str, *, ipv6: bool) -> str:
    """Return the host as the banner shows it, a numeric one as the socket reads it."""
    if ipv6:
        return f"[{host}]"
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return host


def banner(url: str, *, websocket: bool) -> str:
    """Return the lines Django's server prints on start, naming ASGI and the socket."""
    quit_command = "CTRL-BREAK" if sys.platform == "win32" else "CONTROL-C"
    now = datetime.now().astimezone().strftime("%B %d, %Y - %X")
    socket_note = " with the realtime WebSocket" if websocket else ""
    return (
        f"{now}\n"
        f"Django version {get_version()}, using settings {settings.SETTINGS_MODULE!r}\n"
        f"Starting ASGI development server{socket_note} at {url}\n"
        f"Quit the server with {quit_command}."
    )


def development_warning() -> str | None:
    """Return Django's warning against serving production, None when it is hidden."""
    if os.environ.get(_HIDE_WARNING) == "true":
        return None
    return (
        "WARNING: This is a development server. Do not use it in a production "
        "setting. Use a production WSGI or ASGI server instead.\nFor more information "
        "on production servers see: "
        f"https://docs.djangoproject.com/en/{get_docs_version()}/howto/deployment/"
    )


__all__ = [
    "DEV_SERVER_KEY",
    "ASGIServer",
    "UvicornServer",
    "asgi_server",
    "banner",
    "development_application",
    "development_warning",
    "display_host",
    "offers_websocket",
    "project_application",
    "serve",
    "serve_development",
    "server_class",
    "server_installed",
    "server_options",
    "speaks_websocket",
]
