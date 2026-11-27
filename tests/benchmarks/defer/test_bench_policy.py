from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import AsyncRequestFactory, RequestFactory

from next.defer.manager import defer_manager
from next.defer.markers import Delivery
from next.defer.policy import delivery_facts
from next.defer.probe import server_facts
from tests.benchmarks.factories import threaded_get


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest

    from next.defer import DeliveryDecision


_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _AGENT, "Accept-Encoding": "gzip, br"}
_FRESH_ROUNDS = 500
_FRESH_WARMUP_ROUNDS = 20


def _sync_wsgi() -> HttpRequest:
    return RequestFactory().get("/", headers=_HEADERS)


def _threaded_wsgi() -> HttpRequest:
    request = threaded_get("/")
    request.META["HTTP_USER_AGENT"] = _AGENT
    return request


def _asgi() -> HttpRequest:
    return AsyncRequestFactory().get("/", headers=_HEADERS)


_SERVERS = pytest.mark.parametrize(
    ("build", "delivery"),
    [
        pytest.param(_sync_wsgi, Delivery.INLINE, id="wsgi_sync"),
        pytest.param(_threaded_wsgi, Delivery.STREAM, id="wsgi_threaded"),
        pytest.param(_asgi, Delivery.STREAM, id="asgi"),
    ],
)


def _decide(request: HttpRequest) -> DeliveryDecision:
    """Return the verdict of `request` the way a page with deferred calls asks it."""
    return defer_manager.policy().delivery(request, delivery_facts(request))


class TestBenchServerFacts:
    """How a response reaches its client, asked of the server probe per request."""

    @_SERVERS
    @pytest.mark.benchmark(group="defer.policy")
    def test_server_facts(
        self, build: Callable[[], HttpRequest], delivery: Delivery, benchmark
    ) -> None:
        """The backend's own answer, which `server_facts` holds on the request."""
        request = build()
        backend = defer_manager.probe()
        facts = benchmark(backend.facts, request)
        assert facts.incremental is True
        assert facts.concurrent is (delivery is Delivery.STREAM)

    @pytest.mark.benchmark(group="defer.policy")
    def test_server_facts_held(self, benchmark) -> None:
        """Every read after the first one of a request, answered from the request."""
        request = _sync_wsgi()
        first = server_facts(request)
        assert benchmark(server_facts, request) is first


class TestBenchDeliveryPolicy:
    """The delivery verdict of one request, from its facts to the row that decides."""

    @_SERVERS
    @pytest.mark.benchmark(group="defer.policy")
    def test_decision_on_a_fresh_request(
        self, build: Callable[[], HttpRequest], delivery: Delivery, benchmark
    ) -> None:
        """Facts and verdict of a request nothing has read yet, one request a round."""

        def setup() -> tuple[tuple[HttpRequest], dict[str, object]]:
            return (build(),), {}

        assert _decide(build()).delivery is delivery
        benchmark.pedantic(
            _decide,
            setup=setup,
            rounds=_FRESH_ROUNDS,
            warmup_rounds=_FRESH_WARMUP_ROUNDS,
        )

    @_SERVERS
    @pytest.mark.benchmark(group="defer.policy")
    def test_decision_warm(
        self, build: Callable[[], HttpRequest], delivery: Delivery, benchmark
    ) -> None:
        """The same decision on a request whose server facts and intent are held."""
        request = build()
        assert benchmark(_decide, request).delivery is delivery

    @_SERVERS
    @pytest.mark.benchmark(group="defer.policy")
    def test_verdict_alone(
        self, build: Callable[[], HttpRequest], delivery: Delivery, benchmark
    ) -> None:
        """The policy rows over facts already read, bot pattern included."""
        request = build()
        facts = delivery_facts(request)
        policy = defer_manager.policy()
        assert benchmark(policy.delivery, request, facts).delivery is delivery

    @pytest.mark.benchmark(group="defer.policy")
    def test_streams_partials(self, benchmark) -> None:
        """The answer every page render gives the runtime about partial streams."""
        request = _threaded_wsgi()
        policy = defer_manager.policy()
        assert benchmark(policy.streams_partials, request) is True
