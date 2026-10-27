import pytest
from django.contrib.sessions.backends.cache import SessionStore
from django.http import HttpRequest

from next.forms.errors import SessionRotationError
from next.forms.transport import TRANSPORT_SCOPE_KEY, SessionPin, pin_session, tunneled


def _request(scope: dict[str, object] | None = None) -> HttpRequest:
    request = HttpRequest()
    if scope is not None:
        request.scope = scope
    request.session = SessionStore()
    return request


class TestBenchTunneled:
    """Tell a tunneled form action from a plain HTTP one, paid on every action."""

    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            (None, False),
            ({"type": "http"}, False),
            ({"type": "http", TRANSPORT_SCOPE_KEY: "ws"}, True),
        ],
        ids=["wsgi", "asgi", "tunneled"],
    )
    @pytest.mark.benchmark(group="forms.transport")
    def test_tunneled(
        self, scope: dict[str, object] | None, *, expected: bool, benchmark
    ) -> None:
        request = _request(scope)
        assert benchmark(tunneled, request) is expected


class TestBenchPinSession:
    """Shadow the rotating methods of a tunneled action's session."""

    @pytest.mark.benchmark(group="forms.transport")
    def test_cold(self, benchmark) -> None:
        """Each round pins a fresh session, the cost of one tunneled action."""
        requests: list[HttpRequest] = []

        def setup() -> tuple[tuple[HttpRequest], dict[str, object]]:
            requests.append(_request())
            return (requests[-1],), {}

        benchmark.pedantic(pin_session, setup=setup, rounds=500, warmup_rounds=5)
        with pytest.raises(SessionRotationError):
            requests[-1].session.cycle_key()

    @pytest.mark.benchmark(group="forms.transport")
    def test_warm(self, benchmark) -> None:
        """A session already pinned hands back its pin without shadowing again."""
        request = _request()
        pin = pin_session(request)
        assert isinstance(pin, SessionPin)
        assert benchmark(pin_session, request) is pin
