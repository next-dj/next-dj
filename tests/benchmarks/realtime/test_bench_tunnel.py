import asyncio
from collections.abc import Iterator
from types import MappingProxyType, ModuleType

import pytest
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import path as route

from next.forms.transport import ACTION_KIND
from next.realtime.frames import Reply, Request
from next.realtime.limits import TunnelLimits
from next.realtime.tunnel import DjangoTunnel
from tests.support.realtime import websocket_scope


_ACTION = "/_next/form/bench/"
_HANDSHAKE = ((b"cookie", b"csrftoken=secret"), (b"host", b"testserver"))


def _action_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse()


_action_view.next_access_kind = ACTION_KIND


class _ActionApp:
    """Answer every tunneled POST at once, the trivial view behind the URL."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        self.calls += 1
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html"),
                    (b"x-next-transport", b"any"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})


@pytest.fixture()
def action_urls() -> Iterator[None]:
    urls = ModuleType("tunnel_bench_urls")
    urls.urlpatterns = [route(_ACTION.lstrip("/"), _action_view)]
    with override_settings(ROOT_URLCONF=urls):
        yield


@pytest.mark.usefixtures("action_urls")
class TestBenchTunnel:
    """Run one ``http`` frame through the tunnel and shape the reply."""

    @pytest.mark.benchmark(group="realtime.tunnel")
    def test_round_trip(self, bench_loop: asyncio.AbstractEventLoop, benchmark) -> None:
        """Checks the frame, routes it on the pool, runs the app and reads the reply."""
        app = _ActionApp()
        tunnel = DjangoTunnel(
            app, websocket_scope(_HANDSHAKE), TunnelLimits(4, 30.0, 64 * 1024)
        )
        request = Request(
            "1",
            "POST",
            _ACTION,
            MappingProxyType(
                {
                    "content-type": "application/x-www-form-urlencoded",
                    "x-csrftoken": "secret",
                }
            ),
            "choice=2",
        )

        def run() -> object:
            return bench_loop.run_until_complete(tunnel(request, connection="c"))

        reply = benchmark(run)
        assert reply == Reply(
            200, {"content-type": "text/html", "x-next-transport": "any"}, "ok"
        )
        before = app.calls
        assert run() == reply
        assert app.calls == before + 1
