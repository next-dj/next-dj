import asyncio
from collections.abc import Iterator

import fakeredis
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import AsyncRequestFactory, RequestFactory

from next.realtime import MemoryBroker, Presence, TopicStream
from next.realtime.manager import broker_manager
from next.testing import SseReader
from tests.support.realtime import FakeServerBroker, silent_schedule


TOPIC = "room:1"


def identify(user: object, topic: str) -> dict[str, object]:
    return {"id": "guest", "topic": topic}


@pytest.fixture(params=["memory", "redis"])
def broker(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MemoryBroker | FakeServerBroker]:
    backend = (
        MemoryBroker(schedule=silent_schedule)
        if request.param == "memory"
        else FakeServerBroker(fakeredis.FakeServer(), schedule=silent_schedule)
    )
    monkeypatch.setattr(broker_manager, "_backend", backend)
    yield backend
    backend.close()


@pytest.fixture(
    params=[
        (True, Presence(1)),
        (identify, Presence(1, ({"id": "guest", "topic": TOPIC},))),
    ],
    ids=["count", "members"],
)
def presence(request: pytest.FixtureRequest) -> tuple[object, Presence]:
    return request.param


def _async_request():
    request = AsyncRequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    return request


@pytest.mark.django_db()
def test_closing_a_sync_stream_after_hello_leaves(broker, presence) -> None:
    option, expected = presence
    request = RequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    stream = TopicStream(request, TOPIC, presence=option, heartbeat_seconds=5)
    assert next(SseReader(stream)).event == "next-hello"

    assert broker.presence(TOPIC) == expected
    stream.close()
    assert broker.presence(TOPIC) == Presence(0)


@pytest.mark.django_db()
def test_closing_a_sync_stream_before_it_started_records_nothing(broker) -> None:
    request = RequestFactory().get("/rooms/1/stream/")
    stream = TopicStream(request, TOPIC, presence=True, heartbeat_seconds=5)
    stream.close()

    assert broker.presence(TOPIC) == Presence(0)


def test_cancelling_an_asgi_stream_leaves(broker, presence) -> None:
    option, expected = presence
    stream = TopicStream(_async_request(), TOPIC, presence=option, heartbeat_seconds=5)

    async def scenario() -> tuple[Presence, Presence]:
        events = SseReader(stream)
        assert (await anext(events)).event == "next-hello"
        joined = await broker.apresence(TOPIC)
        pull = asyncio.ensure_future(anext(events))
        await asyncio.sleep(0)
        pull.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pull
        return joined, await broker.apresence(TOPIC)

    joined, left = asyncio.run(scenario())
    assert joined == expected
    assert left == Presence(0)


def test_closing_an_asgi_stream_after_hello_leaves(broker, presence) -> None:
    option, expected = presence
    stream = TopicStream(_async_request(), TOPIC, presence=option, heartbeat_seconds=5)

    async def scenario() -> tuple[Presence, Presence]:
        events = SseReader(stream)
        assert (await anext(events)).event == "next-hello"
        joined = await broker.apresence(TOPIC)
        await stream._iterator.aclose()
        return joined, await broker.apresence(TOPIC)

    joined, left = asyncio.run(scenario())
    assert joined == expected
    assert left == Presence(0)
