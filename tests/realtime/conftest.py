from collections.abc import Callable, Iterator

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio
from django.test import override_settings

from next.realtime.backends import MemoryBroker
from next.realtime.manager import broker_manager
from next.realtime.redis import RedisBroker
from next.testing.realtime import SocketTicket
from tests.support.realtime import LiveStreams, issue_session_ticket


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
