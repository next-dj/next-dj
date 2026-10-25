import asyncio
import json
import math
import re
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext
from unittest.mock import create_autospec

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
)
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncRequestFactory, RequestFactory, override_settings

from next.partial import Patches
from next.partial.stream import StreamEnd, StreamHello
from next.realtime import (
    MemoryBroker,
    TopicStream,
    apublish,
    publish,
    topics as realtime_topics,
    transports,
)
from next.realtime.backends import BrokerBackend
from next.realtime.errors import BrokerConnectionError, InvalidTicketError
from next.realtime.messages import Position
from next.realtime.tickets import verify_ticket
from next.testing import SseEvent, SseReader, memory_broker as install_memory_broker
from next.testing.realtime import override_websocket_mount
from tests.support.realtime import LiveStreams, anext_item


def _suppress_message(message):
    return None


def _event_transform(message):
    return Patches(None).event("poll:update", {"seq": message.seq})


def _allow_topic(_user: object, _topic: str) -> bool:
    return True


def _name_guest(_user: object, _topic: str) -> dict[str, object]:
    return {"name": "guest"}


@pytest.fixture()
def ws_available():
    with override_websocket_mount(mounted=True):
        yield


def _body(response: TopicStream) -> dict[str, object]:
    assert isinstance(response, TopicStream)
    assert response["Content-Type"] == "application/json"
    return json.loads(b"".join(response.streaming_content))


@pytest.fixture()
def memory_broker() -> Iterator[MemoryBroker]:
    with install_memory_broker() as backend:
        yield backend


def test_sync_topic_stream_published_message_follows_retry_and_hello(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(
        request, "poll:1", heartbeat_seconds=0.01, server_clock=lambda: 12.345
    )
    with SseReader(stream) as events:
        hello = next(events)
        assert events.retry is not None
        publish("poll:1", Patches(None).refresh(zone="results"), on_commit=False)
        frame = next(events)

    assert hello.event == "next-hello"
    assert hello.json()["protocol"] == 2
    assert hello.json()["topics"] == {"poll:1": {"epoch": "", "seq": 0}}
    epoch = memory_broker.positions(["poll:1"])["poll:1"].epoch
    assert (frame.id, frame.event) == (f"{epoch}:1", "next-patches")
    assert '"zone":"results"' in frame.data
    assert frame.json()["topic"] == "poll:1"
    assert frame.json()["seq"] == 1
    assert frame.json()["epoch"] == epoch
    assert frame.json()["server_time"] == 12345
    assert streams.live() == 0


def test_sync_topic_stream_idle_emits_heartbeats_after_hello(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    stream = TopicStream(RequestFactory().get("/"), "poll:1", heartbeat_seconds=0.01)
    with SseReader(stream):
        iterator = stream.streaming_content
        assert next(iterator).startswith(b"retry: ")
        assert next(iterator).startswith(b"event: next-hello")
        assert next(iterator) == _HEARTBEAT
        assert next(iterator) == _HEARTBEAT
    assert streams.live() == 0


@pytest.mark.usefixtures("ws_available")
def test_sse_hello_announces_only_sse_even_with_a_websocket_mounted(
    memory_broker: MemoryBroker,
) -> None:
    publish("poll:2", Patches(None).refresh(zone="results"), on_commit=False)
    request = RequestFactory().get("/polls/stream/")
    stream = TopicStream(
        request, "poll:2", "poll:1", heartbeat_seconds=0.01, server_clock=lambda: 2.5
    )
    with SseReader(stream) as events:
        body = next(events).json()
    epoch = memory_broker.positions(["poll:2"])["poll:2"].epoch
    assert body == {
        "protocol": 2,
        "server_time": 2500,
        "topics": {
            "poll:2": {"epoch": epoch, "seq": 1},
            "poll:1": {"epoch": "", "seq": 0},
        },
        "transports": ["sse"],
    }


@pytest.mark.django_db()
def test_topic_stream_negotiates_a_private_session_bound_websocket_ticket(
    ws_available: None,
) -> None:
    request = RequestFactory().get("/polls/42/stream/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(request, "poll:42", authorize=_allow_topic)

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert "Cookie" in response["Vary"]
    grant = _body(response)
    claims = verify_ticket(grant["ticket"], request.session.session_key)
    assert grant["protocol"] == 2
    assert grant["url"] == "/_next/ws/"
    assert claims.topics == ("poll:42",)
    assert claims.authorizer_ref == "tests.realtime.test_topics._allow_topic"


@pytest.mark.django_db()
def test_websocket_negotiation_falls_back_without_stable_topic_authorizer(
    ws_available: None,
) -> None:
    request = RequestFactory().get("/polls/42/stream/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(request, "poll:42")

    assert response.status_code == 406
    assert _body(response) == {"transport": "sse", "reason": "authorization"}


@pytest.mark.django_db()
def test_websocket_negotiation_without_get_or_ws_transport_is_refused(
    monkeypatch: pytest.MonkeyPatch, ws_available: None
) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["sse"]}}):
        request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
        response = TopicStream(request, "room", authorize=_allow_topic)
        assert response.status_code == 406
        assert _body(response) == {"error": "transport_unavailable"}

    transports.withdraw_websocket()
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    assert TopicStream(request, "room", authorize=_allow_topic).status_code == 406

    transports.announce_websocket()
    request = RequestFactory().post("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    response = TopicStream(request, "room", authorize=_allow_topic)
    assert response.status_code == 405
    assert response["Allow"] == "GET"


@pytest.mark.django_db()
def test_websocket_negotiation_rejects_unstable_callbacks_and_topics(
    ws_available: None,
) -> None:
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()

    def local_transform(_message):
        return None

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(
            request, "room", transform=local_transform, authorize=_allow_topic
        )
        assert response.status_code == 406
        assert _body(response)["reason"] == "transform"

        def local_authorizer(_user, _topic):
            return True

        response = TopicStream(request, "room", authorize=local_authorizer)
        assert response.status_code == 406
        assert _body(response)["reason"] == "authorization"

        response = TopicStream(
            request, *[f"room:{index}" for index in range(65)], authorize=_allow_topic
        )
        assert response.status_code == 400

        response = TopicStream(request, "bad topic", authorize=_allow_topic)
        assert response.status_code == 400
        assert _body(response) == {"error": "invalid_topics"}


@pytest.mark.django_db()
def test_websocket_negotiation_maps_ticket_failure_to_service_unavailable(
    monkeypatch: pytest.MonkeyPatch, ws_available: None
) -> None:
    monkeypatch.setattr(
        realtime_topics,
        "issue_ticket",
        create_autospec(
            realtime_topics.issue_ticket, side_effect=InvalidTicketError("session")
        ),
    )
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(request, "room", authorize=_allow_topic)
    assert response.status_code == 503
    assert _body(response) == {"error": "session_unavailable"}


@pytest.mark.django_db()
def test_websocket_negotiation_accepts_stable_transform_and_explicit_references(
    ws_available: None,
) -> None:
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(
            request,
            "room",
            transform="tests.realtime.test_topics._event_transform",
            authorize="tests.realtime.test_topics._allow_topic",
        )
    assert response.status_code == 200
    claims = verify_ticket(_body(response)["ticket"], request.session.session_key)
    assert claims.transform_ref == "tests.realtime.test_topics._event_transform"
    assert claims.authorizer_ref == "tests.realtime.test_topics._allow_topic"


@pytest.mark.django_db()
def test_websocket_negotiation_discovers_stable_callbacks_and_rejects_bad_paths(
    ws_available: None,
) -> None:
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(
            request, "room", transform=_event_transform, authorize=_allow_topic
        )
        assert response.status_code == 200
        claims = verify_ticket(_body(response)["ticket"], request.session.session_key)
        assert claims.transform_ref == "tests.realtime.test_topics._event_transform"
        assert claims.authorizer_ref == "tests.realtime.test_topics._allow_topic"

        with pytest.raises(ImproperlyConfigured, match=r"transform=.* does not import"):
            TopicStream(
                request,
                "room",
                transform="missing.module.transform",
                authorize=_allow_topic,
            )
        with pytest.raises(ImproperlyConfigured, match=r"authorize=.* does not import"):
            TopicStream(request, "room", authorize="missing.module.authorizer")

        def renamed(message):
            return message

        renamed.__qualname__ = "renamed_away"
        response = TopicStream(
            request, "room", transform=renamed, authorize=_allow_topic
        )
        assert response.status_code == 406
        assert _body(response)["reason"] == "transform"
        with pytest.raises(ImproperlyConfigured, match="does not name a callable"):
            TopicStream(request, "room", presence="tests.realtime.test_topics._SKIP_ID")


_SKIP_ID = re.compile(r"[0-9a-f]{16}:1")
_HEARTBEAT = b": heartbeat\n\n"


def test_sync_topic_stream_transform_can_suppress_message(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(
        request, "poll:1", transform=_suppress_message, heartbeat_seconds=0.001
    )
    with SseReader(stream) as events:
        assert next(events).event == "next-hello"
        publish("poll:1", Patches(None).refresh(zone="results"), on_commit=False)
        skipped = next(events)
    assert skipped.event == "next-skip"
    assert _SKIP_ID.match(str(skipped.id))
    assert skipped.json()["topic"] == "poll:1"
    assert skipped.json()["seq"] == 1
    assert streams.live() == 0


def test_sync_topic_stream_transform_emits_envelope(
    memory_broker: MemoryBroker,
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(
        request, "poll:1", transform=_event_transform, heartbeat_seconds=0.01
    )
    with SseReader(stream) as events:
        assert next(events).event == "next-hello"
        publish("poll:1", Patches(None).refresh(zone="results"), on_commit=False)
        frame = next(events)
    assert '"name":"poll:update"' in frame.data
    assert frame.json()["topic"] == "poll:1"
    assert frame.json()["seq"] == 1


def test_async_topic_stream_uses_async_subscription_and_transform(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    def transform(message):
        return Patches(None).event("poll:update", {"seq": message.seq})

    async def scenario() -> None:
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(
            request, "poll:1", transform=transform, heartbeat_seconds=0.01
        )
        async with SseReader(stream) as events:
            assert (await anext(events)).event == "next-hello"
            pending = asyncio.ensure_future(anext(events))
            for _ in range(10):
                if streams.live("poll:1"):
                    break
                await asyncio.sleep(0)
            assert streams.live("poll:1")
            await apublish("poll:1", Patches(None).refresh(zone="results"))
            frame = await pending
        assert frame.id is not None
        assert frame.event == "next-patches"
        assert '"name":"poll:update"' in frame.data
        assert frame.json()["topic"] == "poll:1"
        assert frame.json()["seq"] == 1

    asyncio.run(scenario())
    assert streams.live() == 0


def test_async_topic_stream_idle_without_transform_emits_heartbeat(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(request, "poll:1", heartbeat_seconds=0.001)
        iterator = stream.streaming_content.__aiter__()
        assert (await anext(iterator)).startswith(b"retry: ")
        while (chunk := await anext(iterator)) == _HEARTBEAT:
            pass
        assert chunk.startswith(b"event: next-hello")
        assert await anext(iterator) == _HEARTBEAT
        await stream._iterator.aclose()

    asyncio.run(scenario())
    assert streams.live() == 0


def test_async_message_source_passes_timeout_and_untransformed_messages(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(request, "poll:1", heartbeat_seconds=0.001)
        source = stream._async_messages(memory_broker)
        hello = await anext(source)
        assert hello.protocol == 2
        assert hello.topics == {"poll:1": ("", 0)}
        assert await anext(source) is None
        await apublish("poll:1", Patches(None).refresh(zone="results"))
        message = await anext_item(source)
        assert message is not None
        assert message.topic == "poll:1"
        assert await anext(source) is None
        await source.aclose()

    asyncio.run(scenario())
    assert streams.live() == 0


def test_sync_message_source_exits_when_broker_iterator_finishes(
    memory_broker: MemoryBroker,
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(request, "poll:1", heartbeat_seconds=0.01)
    message = memory_broker.publish(
        "poll:1", Patches(None).refresh(zone="results").envelope()
    )

    broker = create_autospec(BrokerBackend, instance=True)
    broker.subscribe.return_value = nullcontext(iter([message]))
    broker.positions.side_effect = lambda topics: {
        topic: Position("", 0) for topic in topics
    }
    items = list(stream._sync_messages(broker))
    assert items[0].protocol == 2
    assert items[1] == message


def test_topic_stream_subscription_precedes_the_position_snapshot(
    memory_broker: MemoryBroker, streams: LiveStreams, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(request, "poll:1", heartbeat_seconds=0.01)
    original_positions = memory_broker.positions
    calls: list[tuple[str, ...]] = []

    def publish_during_snapshot(topics):
        calls.append(tuple(topics))
        assert streams.live("poll:1")
        memory_broker.publish(
            "poll:1", Patches(None).refresh(zone="results").envelope()
        )
        return original_positions(topics)

    monkeypatch.setattr(memory_broker, "positions", publish_during_snapshot)
    source = stream._sync_messages(memory_broker)
    hello = next(source)
    message = next(source)
    source.close()
    assert calls == [("poll:1",)]
    assert hello.topics["poll:1"][1] == 1
    assert hello.topics["poll:1"][0] == message.epoch
    assert message.seq == 1
    assert streams.live() == 0


@pytest.mark.django_db()
def test_websocket_negotiation_subclass_reply_is_a_subclass_instance(
    ws_available: None,
) -> None:
    class AuditedStream(TopicStream):
        def __init__(self, request, *topics, **options) -> None:
            super().__init__(request, *topics, **options)
            self.audited = True

    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = AuditedStream(request, "room", authorize=_allow_topic)
    assert isinstance(response, AuditedStream)
    assert response.audited is True
    assert response.status_code == 200
    assert _body(response)["transport"] == "ws"


@pytest.mark.django_db(transaction=True)
def test_async_negotiation_reply_streams_one_json_chunk(ws_available: None) -> None:
    request = AsyncRequestFactory().get("/updates/", headers={"X-Next-Transport": "ws"})
    request.session = SessionStore()

    async def body() -> list[bytes]:
        return [chunk async for chunk in response.streaming_content]

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(request, "room", authorize=_allow_topic)
    assert response["Content-Type"] == "application/json"
    chunks = asyncio.run(body())
    assert len(chunks) == 1
    assert json.loads(chunks[0])["transport"] == "ws"


@pytest.mark.django_db()
def test_websocket_negotiation_falls_back_for_an_oversized_reference(
    ws_available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime_topics, "MAX_REFERENCE_LENGTH", 8)
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(request, "room", authorize=_allow_topic)
    assert response.status_code == 406
    assert _body(response) == {"transport": "sse", "reason": "authorization"}


def test_topic_stream_without_heartbeat_uses_the_realtime_setting(
    memory_broker: MemoryBroker,
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 7}}):
        stream = TopicStream(request, "poll:1")
    assert stream._subscription_timeout == 7.0
    assert stream._heartbeat_seconds == 7.0


def test_async_topic_stream_transform_can_suppress_message(
    memory_broker: MemoryBroker, streams: LiveStreams, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        subscribed = asyncio.Event()
        original_asubscribe = memory_broker.asubscribe

        @asynccontextmanager
        async def observed_asubscribe(topics, **options):
            async with original_asubscribe(topics, **options) as stream:
                subscribed.set()
                yield stream

        monkeypatch.setattr(memory_broker, "asubscribe", observed_asubscribe)
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(
            request, "poll:1", transform=_suppress_message, heartbeat_seconds=0.1
        )
        async with SseReader(stream) as events:
            assert (await anext(events)).event == "next-hello"
            pending = asyncio.ensure_future(anext(events))
            await asyncio.wait_for(subscribed.wait(), timeout=1)
            await apublish("poll:1", Patches(None).refresh(zone="results"))
            skipped = await pending
        assert skipped.event == "next-skip"
        assert _SKIP_ID.match(str(skipped.id))

    asyncio.run(scenario())
    assert streams.live() == 0


@pytest.mark.parametrize("seconds", [0, -1, math.inf, math.nan, True, "fast"])
def test_topic_stream_invalid_heartbeat_raises(seconds: object) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    with pytest.raises(ValueError, match="positive finite"):
        TopicStream(request, "poll:1", heartbeat_seconds=seconds)


def test_topic_stream_missing_or_invalid_topic_raises() -> None:
    request = RequestFactory().get("/polls/1/stream/")
    with pytest.raises(ValueError, match="at least one topic"):
        TopicStream(request)
    with pytest.raises(ValueError, match="Invalid realtime topic"):
        TopicStream(request, "bad topic")


_END_FRAME = b'event: next-end\ndata: {"code":"auth"}\n\n'


def test_sync_topic_stream_closes_before_subscribing_when_access_is_denied(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(
        request, "poll:1", authorize=lambda _user, _topic: False, heartbeat_seconds=1
    )
    chunks = list(stream.streaming_content)
    assert len(chunks) == 2
    assert chunks[0].startswith(b"retry: ")
    assert chunks[1] == _END_FRAME
    assert streams.live() == 0


def test_sync_topic_stream_closes_once_the_authorizer_revokes_access(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    allowed = [True]
    calls: list[str] = []

    def authorize(_user: object, topic: str) -> bool:
        calls.append(topic)
        return allowed[0]

    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(
        request, "poll:1", authorize=authorize, heartbeat_seconds=0.001
    )
    with SseReader(stream) as events:
        assert next(events).event == "next-hello"
        allowed[0] = False
        rest = list(events)
    assert rest == [SseEvent("next-end", '{"code":"auth"}')]
    assert calls[-1] == "poll:1"
    assert len(calls) >= 2
    assert streams.live() == 0


def test_topic_stream_rechecks_access_at_most_once_per_heartbeat(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    calls: list[str] = []

    def authorize(_user: object, topic: str) -> bool:
        calls.append(topic)
        return True

    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(request, "poll:1", authorize=authorize, heartbeat_seconds=60)
    with SseReader(stream) as events:
        next(events)
        for _ in range(3):
            publish("poll:1", Patches(None).refresh(zone="results"), on_commit=False)
            assert next(events).event == "next-patches"
    assert calls == ["poll:1"]
    assert streams.live() == 0


@pytest.mark.django_db(transaction=True)
def test_async_topic_stream_closes_after_logout(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    user = get_user_model().objects.create_user(username="reader", password="x")
    request = AsyncRequestFactory().get("/polls/1/stream/")
    request.session = SessionStore()
    request.user = user
    store = request.session
    store[SESSION_KEY] = str(user.pk)
    store[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    store.save()

    async def scenario() -> None:
        stream = TopicStream(
            request, "poll:1", authorize=_allow_topic, heartbeat_seconds=0.01
        )
        events = SseReader(stream)
        assert (await anext(events)).event == "next-hello"
        await sync_to_async(store.flush, thread_sensitive=False)()
        assert [event async for event in events] == []
        assert streams.live() == 0
        assert {
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        } == set()

    asyncio.run(scenario())


def test_async_topic_stream_denial_closes_before_subscribing(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(
            request, "poll:1", authorize=lambda _u, _t: False, heartbeat_seconds=1
        )
        chunks = [chunk async for chunk in stream.streaming_content]
        assert len(chunks) == 2
        assert chunks[1] == _END_FRAME
        assert streams.live() == 0

    asyncio.run(scenario())


def test_async_topic_stream_revokes_on_a_message_and_runs_transform_off_loop(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    allowed = [True]
    threads: list[bool] = []

    def authorize(_user: object, _topic: str) -> bool:
        return allowed[0]

    def transform(message):
        threads.append(threading.current_thread() is threading.main_thread())
        return Patches(None).event("poll:update", {"seq": message.seq})

    async def scenario() -> None:
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(
            request,
            "poll:1",
            transform=transform,
            authorize=authorize,
            heartbeat_seconds=0.001,
        )
        source = stream._async_messages(memory_broker)
        assert isinstance(await anext(source), StreamHello)
        await apublish("poll:1", Patches(None).refresh(zone="results"))
        item = await anext_item(source)
        assert item.envelope.ops[0].extras["name"] == "poll:update"
        allowed[0] = False
        await asyncio.sleep(0.01)
        rest = [item async for item in source]
        assert rest[-1] == StreamEnd("auth")
        assert set(rest[:-1]) <= {None}
        assert streams.live() == 0

    asyncio.run(scenario())
    assert threads == [False]


class _DroppingBroker(MemoryBroker):
    def subscribe(self, topics, **options):
        _ = topics, options
        return _raising_context()

    def asubscribe(self, topics, **options):
        _ = topics, options
        return _araising_context()


@contextmanager
def _raising_context():
    msg = "reconnected"
    raise BrokerConnectionError(msg)
    yield


@asynccontextmanager
async def _araising_context():
    msg = "reconnected"
    raise BrokerConnectionError(msg)
    yield


def test_sync_topic_stream_ends_when_the_broker_drops_the_subscription() -> None:
    request = RequestFactory().get("/polls/1/stream/")
    stream = TopicStream(request, "poll:1", heartbeat_seconds=1)
    assert list(stream._sync_messages(_DroppingBroker())) == []


def test_async_topic_stream_ends_when_the_broker_drops_the_subscription() -> None:
    async def scenario() -> None:
        request = AsyncRequestFactory().get("/polls/1/stream/")
        stream = TopicStream(request, "poll:1", heartbeat_seconds=1)
        items = [item async for item in stream._async_messages(_DroppingBroker())]
        assert items == []

    asyncio.run(scenario())


def _anonymous_session_request(factory):
    request = factory.get("/polls/1/stream/")
    request.session = SessionStore()
    request.session.create()
    request.user = AnonymousUser()
    return request


@pytest.mark.django_db(transaction=True)
def test_sync_anonymous_session_rotation_ends_without_an_end_frame(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = _anonymous_session_request(RequestFactory())
    stream = TopicStream(request, "poll:1", heartbeat_seconds=0.001)
    events = SseReader(stream)
    assert next(events).event == "next-hello"
    SessionStore(session_key=request.session.session_key).cycle_key()
    assert list(events) == []
    assert streams.live() == 0


@pytest.mark.django_db(transaction=True)
def test_async_anonymous_session_rotation_ends_without_an_end_frame(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = _anonymous_session_request(AsyncRequestFactory())

    async def scenario() -> None:
        stream = TopicStream(request, "poll:1", heartbeat_seconds=0.001)
        events = SseReader(stream)
        assert (await anext(events)).event == "next-hello"
        store = SessionStore(session_key=request.session.session_key)
        await sync_to_async(store.cycle_key, thread_sensitive=False)()
        assert [event async for event in events] == []
        assert streams.live() == 0

    asyncio.run(scenario())


def _stale_anonymous_request(factory):
    request = _anonymous_session_request(factory)
    SessionStore(session_key=request.session.session_key).delete()
    return request


@pytest.mark.django_db(transaction=True)
def test_sync_stale_anonymous_cookie_at_open_still_streams(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = _stale_anonymous_request(RequestFactory())
    stream = TopicStream(
        request, "poll:1", authorize=_allow_topic, heartbeat_seconds=60
    )
    with SseReader(stream) as events:
        assert next(events).event == "next-hello"
        publish("poll:1", Patches(None).refresh(zone="results"), on_commit=False)
        assert next(events).event == "next-patches"
    assert streams.live() == 0


@pytest.mark.django_db(transaction=True)
def test_async_stale_anonymous_cookie_at_open_still_streams(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    request = _stale_anonymous_request(AsyncRequestFactory())

    async def scenario() -> None:
        stream = TopicStream(
            request, "poll:1", authorize=_allow_topic, heartbeat_seconds=60
        )
        async with SseReader(stream) as events:
            assert (await anext(events)).event == "next-hello"
            await apublish("poll:1", Patches(None).refresh(zone="results"))
            assert (await anext(events)).event == "next-patches"
        assert streams.live() == 0

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_stale_cookie_of_an_authenticated_user_is_a_revocation(
    memory_broker: MemoryBroker, streams: LiveStreams
) -> None:
    user = get_user_model().objects.create_user(username="gone", password="x")
    request = _stale_anonymous_request(RequestFactory())
    request.user = user
    stream = TopicStream(request, "poll:1", authorize=_allow_topic, heartbeat_seconds=1)
    chunks = list(stream.streaming_content)
    assert len(chunks) == 1
    assert chunks[0].startswith(b"retry: ")
    assert streams.live() == 0


@pytest.mark.django_db(transaction=True)
def test_websocket_negotiation_replaces_a_stale_anonymous_cookie(
    ws_available: None,
) -> None:
    request = _stale_anonymous_request(RequestFactory())
    request.META["HTTP_X_NEXT_TRANSPORT"] = "ws"
    stale = request.session.session_key
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(request, "room", authorize=_allow_topic)
    grant = _body(response)
    assert response.status_code == 200
    assert request.session.session_key != stale
    assert request.session.modified is True
    fresh = SessionStore(session_key=request.session.session_key)
    assert verify_ticket(grant["ticket"], fresh.session_key).topics == ("room",)


_NAME_GUEST = "tests.realtime.test_topics._name_guest"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("presence", "expected"),
    [
        (False, False),
        (True, True),
        (_name_guest, _NAME_GUEST),
        (_NAME_GUEST, _NAME_GUEST),
    ],
    ids=["off", "count", "callable", "dotted"],
)
def test_websocket_ticket_carries_the_stream_presence(
    ws_available: None, presence: object, expected: object
) -> None:
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(
            request, "room", authorize=_allow_topic, presence=presence
        )
    assert response.status_code == 200
    claims = verify_ticket(_body(response)["ticket"], request.session.session_key)
    assert claims.presence == expected


@pytest.mark.django_db()
def test_websocket_negotiation_falls_back_for_an_unstable_identify(
    ws_available: None,
) -> None:
    request = RequestFactory().get("/updates/", HTTP_X_NEXT_TRANSPORT="ws")
    request.session = SessionStore()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TRANSPORTS": ["ws"]}}):
        response = TopicStream(
            request, "room", authorize=_allow_topic, presence=lambda _u, _t: None
        )
    assert response.status_code == 406
    assert _body(response) == {"transport": "sse", "reason": "presence"}
