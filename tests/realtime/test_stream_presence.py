import asyncio
from collections.abc import Iterator

import fakeredis
import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import AsyncRequestFactory, RequestFactory

from next.realtime import MemoryBroker, Presence, TopicStream
from next.realtime.manager import broker_manager
from next.realtime.socket import Handshake
from next.testing import SseReader
from tests.support.realtime import FakeServerBroker, silent_schedule


TOPIC = "room:1"


def identify(user: object, topic: str) -> dict[str, object]:
    return {"id": "guest", "topic": topic}


def identify_guest(user: object, topic: str, subject: str) -> dict[str, object]:
    return {"name": f"Guest {subject[:4]}"}


def _with_session(request):
    """Attach a saved session's cookie, the credential an SSE subject is derived from."""
    session = SessionStore()
    session.save()
    request.COOKIES[settings.SESSION_COOKIE_NAME] = session.session_key
    request.user = AnonymousUser()
    return request, session.session_key


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
        await events.aclose()
        return joined, await broker.apresence(TOPIC)

    joined, left = asyncio.run(scenario())
    assert joined == expected
    assert left == Presence(0)


@pytest.mark.django_db()
def test_a_sync_stream_passes_the_websocket_subject_to_identify(broker) -> None:
    request, key = _with_session(RequestFactory().get("/rooms/1/stream/"))
    subject = Handshake(key, AnonymousUser(), None).subject
    stream = TopicStream(request, TOPIC, presence=identify_guest, heartbeat_seconds=5)
    assert next(SseReader(stream)).event == "next-hello"

    assert broker.presence(TOPIC).members == ({"name": f"Guest {subject[:4]}"},)
    assert subject
    stream.close()


@pytest.mark.django_db(transaction=True)
def test_an_asgi_stream_passes_the_websocket_subject_to_identify(broker) -> None:
    request, key = _with_session(AsyncRequestFactory().get("/rooms/1/stream/"))
    subject = Handshake(key, AnonymousUser(), None).subject
    stream = TopicStream(request, TOPIC, presence=identify_guest, heartbeat_seconds=5)

    async def scenario() -> Presence:
        events = SseReader(stream)
        assert (await anext(events)).event == "next-hello"
        joined = await broker.apresence(TOPIC)
        await events.aclose()
        return joined

    assert asyncio.run(scenario()).members == ({"name": f"Guest {subject[:4]}"},)


@pytest.mark.django_db()
def test_a_stream_without_a_credential_passes_an_empty_subject(broker) -> None:
    request = RequestFactory().get("/rooms/1/stream/")
    request.user = AnonymousUser()
    stream = TopicStream(request, TOPIC, presence=identify_guest, heartbeat_seconds=5)
    assert next(SseReader(stream)).event == "next-hello"

    assert broker.presence(TOPIC).members == ({"name": "Guest "},)
    stream.close()
