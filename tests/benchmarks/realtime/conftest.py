from collections.abc import Iterator

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio
from django.contrib.sessions.backends.cache import SessionStore
from django.http import HttpRequest
from django.test import override_settings

from next.realtime import MemoryBroker, RedisBroker
from next.realtime.manager import broker_manager


_REDIS_BACKEND = "next.realtime.RedisBroker"
_MEMORY_BACKEND = "next.realtime.MemoryBroker"
_CACHE_SESSIONS = "django.contrib.sessions.backends.cache"


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    """Route every Redis client the broker builds to one in-process fake server."""
    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(redis.Redis, "from_url", lambda _url, **_kwargs: sync)
    monkeypatch.setattr(
        redis.asyncio.Redis,
        "from_url",
        lambda _url, **_kwargs: fakeredis.aioredis.FakeRedis(
            server=server, decode_responses=True
        ),
    )
    return sync


@pytest.fixture()
def memory_broker() -> MemoryBroker:
    """Build a fresh process-local broker."""
    return MemoryBroker()


@pytest.fixture()
def redis_broker(fake_redis: fakeredis.FakeRedis) -> Iterator[RedisBroker]:
    """Build a Redis broker backed by the fake server."""
    broker = RedisBroker({"OPTIONS": {"URL": "redis://fake/1"}})
    yield broker
    broker.close()


@pytest.fixture(params=["memory", "redis"])
def any_broker(request: pytest.FixtureRequest) -> MemoryBroker | RedisBroker:
    """Provide each broker implementation in turn."""
    return request.getfixturevalue(f"{request.param}_broker")


@pytest.fixture(params=["memory", "redis"])
def configured_broker(
    request: pytest.FixtureRequest,
) -> Iterator[MemoryBroker | RedisBroker]:
    """Select the broker through ``REALTIME.BROKER`` and yield the built instance."""
    if request.param == "redis":
        request.getfixturevalue("fake_redis")
        config = {"BACKEND": _REDIS_BACKEND, "OPTIONS": {"URL": "redis://fake/1"}}
    else:
        config = {"BACKEND": _MEMORY_BACKEND}
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"BROKER": config}}):
        yield broker_manager.get()


@pytest.fixture()
def session_request() -> Iterator[HttpRequest]:
    """Carry a saved session from the cache engine, the live path without a database."""
    with override_settings(SESSION_ENGINE=_CACHE_SESSIONS):
        request = HttpRequest()
        request.session = SessionStore()
        request.session.save()
        yield request
