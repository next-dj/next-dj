import sys
from collections.abc import Generator
from types import ModuleType

import pytest
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.realtime.frames import MAX_INBOUND_BYTES
from next.realtime.transports import (
    active_transports,
    announce_websocket,
    withdraw_websocket,
)
from next.server import asgi
from next.server.asgi import (
    UvicornServer,
    asgi_server,
    banner,
    development_application,
    development_warning,
    display_host,
    offers_websocket,
    project_application,
    serve,
    serve_development,
    server_class,
    server_options,
    speaks_websocket,
)
from next.testing import override_next_settings, override_websocket_mount


async def named_app(scope, receive, send) -> None:
    """Stand in for the app a project names in `ASGI_APPLICATION`."""


class RecordingServer:
    """A development server a project names in `DEV_SERVER`."""

    def run(self, app, **options: object) -> None:
        """Serve nothing."""


class Runless:
    """A class without the `run` method of `ASGIServer`."""


not_a_class = RecordingServer()


@pytest.fixture(autouse=True)
def _withdrawn_websocket() -> Generator[None, None, None]:
    with override_websocket_mount():
        yield


class TestAsgiServer:
    """uvicorn is optional, so its absence reads as no ASGI server."""

    def test_returns_the_uvicorn_server_by_default(self) -> None:
        assert isinstance(asgi_server(), UvicornServer)

    def test_returns_none_without_uvicorn(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "uvicorn", None)
        assert asgi_server() is None

    def test_the_uvicorn_server_runs_the_app_on_uvicorn(self, monkeypatch) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        fake = ModuleType("uvicorn")
        fake.run = lambda app, **options: calls.append((app, options))
        monkeypatch.setitem(sys.modules, "uvicorn", fake)
        UvicornServer().run(named_app, port=1)
        assert calls == [(named_app, {"port": 1})]

    def test_the_uvicorn_server_maps_the_socket_options(self, monkeypatch) -> None:
        calls: list[tuple[object, dict[str, object]]] = []
        fake = ModuleType("uvicorn")
        fake.run = lambda app, **options: calls.append((app, options))
        monkeypatch.setitem(sys.modules, "uvicorn", fake)
        UvicornServer().run(named_app, max_frame_bytes=1024, ping_interval=None)
        assert calls == [(named_app, {"ws_max_size": 1024, "ws_ping_interval": None})]

    def test_builds_the_server_the_setting_names(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "uvicorn", None)
        with override_next_settings(DEV_SERVER=f"{__name__}.RecordingServer"):
            assert isinstance(asgi_server(), RecordingServer)

    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("Missing", "does not import"),
            ("Runless", "is no class with the run"),
            ("not_a_class", "is no class with the run"),
        ],
    )
    def test_refuses_a_path_that_names_no_server(self, name, message) -> None:
        with pytest.raises(ImproperlyConfigured, match=message):
            server_class(f"{__name__}.{name}")

    def test_speaks_websocket_with_a_library(self) -> None:
        assert speaks_websocket() is True

    def test_no_library_speaks_no_websocket(self, monkeypatch) -> None:
        monkeypatch.setattr(asgi.importlib.util, "find_spec", lambda _: None)
        assert speaks_websocket() is False


class TestProjectApplication:
    """`ASGI_APPLICATION` names the app, else Django's app gets the realtime socket."""

    def test_defaults_to_django_under_the_socket(self) -> None:
        app = project_application()
        assert type(app).__name__ == "_Application"
        assert active_transports() == ("ws", "sse")

    @override_settings(ASGI_APPLICATION="tests.server.test_asgi.named_app")
    def test_reads_the_setting(self) -> None:
        assert project_application() is named_app

    @override_settings(ASGI_APPLICATION="tests.server.test_asgi.missing_app")
    def test_refuses_a_path_that_does_not_import(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="missing_app"):
            project_application()


class TestDevelopmentApplication:
    """Static serving wraps the whole app, so the socket stays reachable."""

    @override_settings(ASGI_APPLICATION="tests.server.test_asgi.named_app")
    def test_serves_static_files_when_asked(self) -> None:
        app = development_application(static=True)
        assert isinstance(app, ASGIStaticFilesHandler)
        assert app.application is named_app

    @override_settings(ASGI_APPLICATION="tests.server.test_asgi.named_app")
    def test_returns_the_project_app_otherwise(self) -> None:
        assert development_application(static=False) is named_app


def _recording_server() -> tuple[ModuleType, list[tuple[object, dict[str, object]]]]:
    calls: list[tuple[object, dict[str, object]]] = []
    server = ModuleType("uvicorn")
    server.run = lambda app, **options: calls.append((app, options))
    return server, calls


class TestServe:
    """The reloader belongs to Django, so uvicorn runs without its own."""

    def test_runs_the_app_with_the_socket_options(self) -> None:
        server, calls = _recording_server()
        serve(server, named_app, host="::1", port=8001)
        assert calls == [
            (
                named_app,
                {
                    "host": "::1",
                    "port": 8001,
                    "max_frame_bytes": MAX_INBOUND_BYTES,
                    "ping_interval": None,
                },
            )
        ]

    def test_options_override_the_defaults(self) -> None:
        server, calls = _recording_server()
        serve(
            server,
            named_app,
            host="::1",
            port=8001,
            options={"max_frame_bytes": 1024, "log_level": "debug"},
        )
        assert calls[0][1] == {
            "host": "::1",
            "port": 8001,
            "max_frame_bytes": 1024,
            "ping_interval": None,
            "log_level": "debug",
        }

    def test_the_defaults_leave_lifespan_to_uvicorn(self) -> None:
        assert "lifespan" not in server_options()


class TestServeDevelopment:
    """The banner runs once the app is built, so it sees the socket the app mounts."""

    def test_announces_the_mounted_socket_then_serves(self) -> None:
        server, calls = _recording_server()
        seen: list[bool] = []
        serve_development(
            server,
            host="127.0.0.1",
            port=8000,
            static=False,
            announce=lambda: seen.append(offers_websocket()),
        )
        assert seen == [True]
        assert type(calls[0][0]).__name__ == "_Application"

    @override_settings(ASGI_APPLICATION="tests.server.test_asgi.named_app")
    def test_serves_without_an_announcement(self) -> None:
        server, calls = _recording_server()
        serve_development(server, host="127.0.0.1", port=8000, static=True)
        app = calls[0][0]
        assert isinstance(app, ASGIStaticFilesHandler)
        assert app.application is named_app


class TestOffersWebsocket:
    """Pages offer the socket once an app in this process mounts it."""

    def test_follows_the_mount(self) -> None:
        withdraw_websocket()
        assert offers_websocket() is False
        announce_websocket()
        assert offers_websocket() is True


class TestBanner:
    """The banner follows Django's, naming ASGI and the socket only when served."""

    @pytest.mark.parametrize(
        ("host", "ipv6", "shown"),
        [
            ("127.1", False, "127.0.0.1"),
            ("localhost", False, "localhost"),
            ("::1", True, "[::1]"),
        ],
    )
    def test_shows_the_host_the_socket_binds(self, host, ipv6, shown) -> None:
        assert display_host(host, ipv6=ipv6) == shown

    def test_names_the_socket(self) -> None:
        text = banner("http://127.0.0.1:8000/", websocket=True)
        assert (
            "Starting ASGI development server with the realtime WebSocket at "
            "http://127.0.0.1:8000/\n" in text
        )

    def test_names_only_asgi_without_the_socket(self, monkeypatch) -> None:
        monkeypatch.setattr(asgi.sys, "platform", "win32")
        text = banner("http://127.0.0.1:8000/", websocket=False)
        assert "Starting ASGI development server at http://127.0.0.1:8000/" in text
        assert text.endswith("Quit the server with CTRL-BREAK.")

    def test_warns_against_production(self, monkeypatch) -> None:
        monkeypatch.delenv("DJANGO_RUNSERVER_HIDE_WARNING", raising=False)
        warning = development_warning()
        assert warning is not None
        assert warning.startswith("WARNING: This is a development server.")

    def test_hides_the_warning_as_django_does(self, monkeypatch) -> None:
        monkeypatch.setenv("DJANGO_RUNSERVER_HIDE_WARNING", "true")
        assert development_warning() is None
