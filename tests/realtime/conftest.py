from collections.abc import Callable, Iterator

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio
from django.test import override_settings

from next.realtime.backends import BrokerBackend, MemoryBroker
from next.realtime.manager import broker_manager
from next.realtime.redis import RedisBroker
from next.testing import memory_broker as install_memory_broker
from next.testing.realtime import SocketTicket, override_websocket_mount
from tests.support.realtime import (
    LiveStreams,
    ServerClock,
    WireOpener,
    issue_session_ticket,
)


@pytest.fixture(autouse=True)
def _reset_broker_manager():
    broker_manager.reset()
    yield
    broker_manager.reset()


@pytest.fixture()
def streams() -> Iterator[LiveStreams]:
    with LiveStreams() as recording:
        yield recording


@pytest.fixture()
def clock() -> ServerClock:
    """Hand a test the clock it moves by hand, starting at zero."""
    return ServerClock(0.0)


@pytest.fixture()
def memory_broker() -> Iterator[MemoryBroker]:
    """Serve realtime from a fresh memory broker for the test."""
    with install_memory_broker() as backend:
        yield backend


@pytest.fixture()
def session_ticket(transactional_db: None) -> SocketTicket:
    """Issue the default ``room:42`` ticket against a fresh saved session."""
    return issue_session_ticket()


@pytest.fixture(params=["memory", "redis"])
def make_broker(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., MemoryBroker | RedisBroker]:
    """Build a replaying memory broker or a Redis broker on one fakeredis server."""
    server = fakeredis.FakeServer()

    def sync_client(url: str, **kwargs: object) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=server, decode_responses=True)

    def async_client(url: str, **kwargs: object) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(redis.Redis, "from_url", sync_client)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", async_client)

    def build(size: int = 10) -> MemoryBroker | RedisBroker:
        replay = {"SIZE": size, "TTL": 60}
        with override_settings(NEXT_FRAMEWORK={"REALTIME": {"REPLAY": replay}}):
            return MemoryBroker() if request.param == "memory" else RedisBroker()

    return build


@pytest.fixture()
def replaying_broker(
    make_broker: Callable[..., BrokerBackend], monkeypatch: pytest.MonkeyPatch
) -> BrokerBackend:
    """Serve realtime from a replaying memory or Redis broker for the test."""
    backend = make_broker()
    monkeypatch.setattr(broker_manager, "_backend", backend)
    return backend


@pytest.fixture(params=["sse-sync", "sse-async", "ws"])
def wire(
    request: pytest.FixtureRequest, transactional_db: None
) -> Iterator[WireOpener]:
    """Open a client of one topic over each transport, the WebSocket one mounted."""
    with override_websocket_mount():
        yield WireOpener(request.param)
