from collections.abc import Generator
from io import StringIO
from types import ModuleType

import pytest
from django.conf import settings
from django.contrib.staticfiles.handlers import (
    ASGIStaticFilesHandler,
    StaticFilesHandler,
)
from django.core.handlers.wsgi import WSGIHandler
from django.core.management import call_command, get_commands
from django.core.management.commands import runserver as django_runserver
from django.test import override_settings

from next.management.commands import runserver
from next.management.commands.runserver import Command
from next.realtime.frames import MAX_INBOUND_BYTES
from next.realtime.transports import announce_websocket
from next.testing import override_websocket_mount


pytestmark = pytest.mark.django_db

SOCKET = {"ws_max_size": MAX_INBOUND_BYTES, "ws_ping_interval": None}
NAMED_APP = "tests.management.commands.test_runserver.named_app"


async def named_app(scope, receive, send) -> None:
    """Stand in for the app a project names in `ASGI_APPLICATION`."""


class ExitedError(Exception):
    """Raised in place of `os._exit`, which would end the test process."""


@pytest.fixture(autouse=True)
def _withdrawn_websocket() -> Generator[None, None, None]:
    with override_websocket_mount():
        yield


@pytest.fixture()
def uvicorn(monkeypatch) -> list[tuple[object, dict[str, object]]]:
    """Swap uvicorn for a module whose `run` only records its call."""
    calls: list[tuple[object, dict[str, object]]] = []
    server = ModuleType("uvicorn")
    server.run = lambda app, **options: calls.append((app, options))
    monkeypatch.setattr(runserver, "asgi_server", lambda: server)
    return calls


def _run(*args: str, **options: object) -> tuple[str, str]:
    out, err = StringIO(), StringIO()
    call_command(Command(), "--noreload", *args, stdout=out, stderr=err, **options)
    return out.getvalue(), err.getvalue()


def _apps(*names: str) -> override_settings:
    return override_settings(INSTALLED_APPS=list(names))


class TestCommandOrder:
    """Django runs the `runserver` of the app listed first."""

    def test_staticfiles_above_next_keeps_its_command(self) -> None:
        with _apps("django.contrib.staticfiles", "next"):
            assert get_commands()["runserver"] == "django.contrib.staticfiles"

    def test_next_above_staticfiles_runs_its_command(self) -> None:
        with _apps("next", "django.contrib.staticfiles"):
            assert get_commands()["runserver"] == "next"


class TestUvicorn:
    """With uvicorn installed the project serves over ASGI on Django's address."""

    @pytest.fixture(autouse=True)
    def _named_app(self, settings) -> None:
        settings.ASGI_APPLICATION = NAMED_APP

    def test_serves_the_project_app_on_the_default_address(self, uvicorn) -> None:
        out, err = _run(skip_checks=False)
        assert uvicorn == [(named_app, {"host": "127.0.0.1", "port": 8000, **SOCKET})]
        assert "Performing system checks..." in out
        assert "System check identified" in out + err
        assert "Starting ASGI development server at http://127.0.0.1:8000/" in out
        assert f"using settings {settings.SETTINGS_MODULE!r}" in out
        assert "Quit the server with CONTROL-C." in out
        assert "WARNING: This is a development server." in out
        assert "WebSocket library" not in err

    def test_names_the_socket_the_app_mounts(self, uvicorn, settings) -> None:
        settings.ASGI_APPLICATION = None
        out, err = _run()
        assert (
            "Starting ASGI development server with the realtime WebSocket at "
            "http://127.0.0.1:8000/" in out
        )
        assert "WebSocket library" not in err

    def test_hides_the_warning_as_django_does(self, uvicorn, monkeypatch) -> None:
        monkeypatch.setenv("DJANGO_RUNSERVER_HIDE_WARNING", "true")
        out, _ = _run()
        assert "WARNING" not in out

    def test_skips_the_checks_when_asked(self, uvicorn) -> None:
        out, _ = _run("--skip-checks", skip_checks=True)
        assert "Performing system checks" not in out
        assert len(uvicorn) == 1

    @pytest.mark.parametrize(
        ("addrport", "host", "port", "shown"),
        [
            ("0:9000", "0", 9000, "http://0.0.0.0:9000/"),
            ("127.1:9001", "127.1", 9001, "http://127.0.0.1:9001/"),
            ("[::1]:8001", "::1", 8001, "http://[::1]:8001/"),
            ("localhost:8002", "localhost", 8002, "http://localhost:8002/"),
        ],
    )
    def test_binds_the_address_django_parses(
        self, uvicorn, addrport, host, port, shown
    ) -> None:
        out, _ = _run(addrport)
        assert uvicorn[0][1] == {"host": host, "port": port, **SOCKET}
        assert shown in out

    def test_ipv6_flag_binds_the_ipv6_loopback(self, uvicorn) -> None:
        out, _ = _run("--ipv6")
        assert uvicorn[0][1] == {"host": "::1", "port": 8000, **SOCKET}
        assert "http://[::1]:8000/" in out

    @override_settings(DEBUG=True)
    def test_serves_static_files_in_debug(self, uvicorn) -> None:
        _run()
        app = uvicorn[0][0]
        assert isinstance(app, ASGIStaticFilesHandler)
        assert app.application is named_app

    @override_settings(DEBUG=True)
    def test_nostatic_leaves_the_app_bare(self, uvicorn) -> None:
        _run("--nostatic")
        assert uvicorn[0][0] is named_app

    def test_insecure_serves_static_files_without_debug(self, uvicorn) -> None:
        _run("--insecure")
        assert isinstance(uvicorn[0][0], ASGIStaticFilesHandler)

    def test_warns_when_no_websocket_library_is_installed(
        self, uvicorn, monkeypatch
    ) -> None:
        announce_websocket()
        monkeypatch.setattr(runserver, "speaks_websocket", lambda: False)
        out, err = _run()
        assert "uvicorn found no WebSocket library" in err
        assert "Starting ASGI development server at" in out

    def test_exits_the_process_when_the_server_fails_to_start(
        self, monkeypatch
    ) -> None:
        def fail(app: object, **options: object) -> None:
            raise SystemExit(3)

        def exit_process(code: int) -> None:
            raise ExitedError(code)

        server = ModuleType("uvicorn")
        server.run = fail
        monkeypatch.setattr(runserver, "asgi_server", lambda: server)
        monkeypatch.setattr(runserver.os, "_exit", exit_process)
        with pytest.raises(ExitedError) as exited:
            _run()
        assert exited.value.args == (1,)

    @pytest.mark.parametrize(
        ("message", "last"), [("Bye.", "Bye."), ("", "Quit the server with CONTROL-C.")]
    )
    def test_interrupt_prints_the_shutdown_message(
        self, monkeypatch, message, last
    ) -> None:
        def interrupt(app: object, **options: object) -> None:
            raise KeyboardInterrupt

        server = ModuleType("uvicorn")
        server.run = interrupt
        monkeypatch.setattr(runserver, "asgi_server", lambda: server)
        monkeypatch.setenv("DJANGO_RUNSERVER_HIDE_WARNING", "true")
        out = StringIO()
        with pytest.raises(SystemExit) as exited:
            call_command(Command(), "--noreload", shutdown_message=message, stdout=out)
        assert exited.value.code == 0
        assert out.getvalue().splitlines()[-1] == last


class TestWithoutUvicorn:
    """Without uvicorn the command is Django's runserver with staticfiles."""

    @pytest.fixture()
    def wsgi(self, monkeypatch) -> list[tuple[object, ...]]:
        calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(runserver, "asgi_server", lambda: None)
        monkeypatch.setattr(
            django_runserver, "run", lambda *args, **kwargs: calls.append(args)
        )
        return calls

    @override_settings(DEBUG=True)
    def test_serves_wsgi_with_static_files(self, wsgi) -> None:
        _run("--skip-checks")
        addr, port, handler = wsgi[0]
        assert (addr, port) == ("127.0.0.1", 8000)
        assert isinstance(handler, StaticFilesHandler)

    @override_settings(DEBUG=True)
    def test_nostatic_serves_the_bare_handler(self, wsgi) -> None:
        _run("--skip-checks", "--nostatic")
        handler = wsgi[0][2]
        assert isinstance(handler, WSGIHandler)
        assert not isinstance(handler, StaticFilesHandler)

    def test_takes_no_static_options_without_staticfiles(self, wsgi) -> None:
        with _apps("next"), override_settings(MIDDLEWARE=[]):
            parser = Command().create_parser("manage.py", "runserver")
            dests = {action.dest for action in parser._actions}
            _run("--skip-checks")
        assert not {"use_static_handler", "insecure_serving"} & dests
        assert not isinstance(wsgi[0][2], StaticFilesHandler)
