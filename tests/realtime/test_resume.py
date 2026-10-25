import asyncio
import json
from collections.abc import Callable

import pytest
from django.core.handlers.asgi import ASGIRequest
from django.test import AsyncRequestFactory, RequestFactory

from next.partial import Patches
from next.partial.stream import StreamEnd, StreamHello, StreamSkip
from next.realtime import Message, TopicStream
from next.realtime.access import Access
from next.realtime.backends import BrokerBackend
from next.realtime.manager import broker_manager
from tests.support.realtime import (
    PlainBroker,
    ResumingClient,
    anext_frame,
    next_frame,
    refresh_envelope,
)


_MAX_PULLS = 50
_DONE = object()


@pytest.fixture()
def broker(
    make_broker: Callable[..., BrokerBackend], monkeypatch: pytest.MonkeyPatch
) -> BrokerBackend:
    backend = make_broker()
    monkeypatch.setattr(broker_manager, "_backend", backend)
    return backend


def _publish_during(
    broker: BrokerBackend, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish one live message the first time the stream reaches a broker step."""
    fired: list[bool] = []
    sync_step = getattr(broker, step)
    async_step = getattr(broker, f"a{step}")

    def fire() -> None:
        if not fired:
            fired.append(True)
            broker.publish("poll:1", refresh_envelope())

    def publishing(*args: object) -> object:
        fire()
        return sync_step(*args)

    async def apublishing(*args: object) -> object:
        fire()
        return await async_step(*args)

    monkeypatch.setattr(broker, step, publishing)
    monkeypatch.setattr(broker, f"a{step}", apublishing)


def _published(broker: BrokerBackend, count: int, topic: str = "poll:1") -> str:
    messages = [broker.publish(topic, refresh_envelope()) for _ in range(count)]
    return messages[0].epoch


def _stream(*topics: str, asynchronous: bool, cursor: str | None = None) -> TopicStream:
    factory = AsyncRequestFactory() if asynchronous else RequestFactory()
    headers = {} if cursor is None else {"Last-Event-ID": cursor}
    request = factory.get("/polls/1/stream/", headers=headers)
    return TopicStream(request, *topics, heartbeat_seconds=0.01)


def _pulled(item: object, items: list[object], count: int) -> bool:
    """Keep a non-heartbeat item and tell whether pulling should stop."""
    if item is _DONE:
        return True
    if item is not None:
        items.append(item)
    return len(items) == count


def _items(stream: TopicStream, broker: BrokerBackend, count: int) -> list[object]:
    """Pull the first non-heartbeat items of a sync or async topic source, or all."""
    items: list[object] = []

    async def collect() -> None:
        source = stream._async_messages(broker)
        for _ in range(_MAX_PULLS):
            if _pulled(await anext(source, _DONE), items, count):
                break
        await source.aclose()

    if isinstance(stream._request, ASGIRequest):
        asyncio.run(collect())
        return items
    source = stream._sync_messages(broker)
    for _ in range(_MAX_PULLS):
        if _pulled(next(source, _DONE), items, count):
            break
    source.close()
    return items


def _trace(items: list[object]) -> list[object]:
    """Name each item by its kind and seq so an order reads at a glance."""
    trace: list[object] = []
    for item in items:
        if isinstance(item, StreamHello):
            trace.append(("hello", item.topics["poll:1"][1]))
        elif isinstance(item, StreamSkip):
            trace.append(("skip", item.seq))
        elif isinstance(item, Message):
            trace.append(item.seq)
        else:
            trace.append(item)
    return trace


@pytest.fixture(params=["sync", "async"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def test_last_event_id_replays_the_missed_messages_before_hello(
    mode: str, broker: BrokerBackend
) -> None:
    epoch = _published(broker, 3)
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=f"{epoch}:1")
    items = _items(stream, broker, 3)
    assert _trace(items) == [2, 3, ("hello", 3)]
    assert items[-1].topics == {"poll:1": (epoch, 3)}


@pytest.mark.parametrize(
    "header",
    [
        "garbage",
        "{epoch}:01",
        "{epoch}:-1",
        "{epoch}:1:2",
        "{EPOCH}:1",
        "{epoch}:1\r\nid: 0",
        "{epoch}:" + "1" * 64,
        "0123456789abcdeg:1",
        "{epoch}:1\n",
        "",
    ],
)
def test_malformed_last_event_id_means_no_cursor(
    mode: str, broker: BrokerBackend, header: str
) -> None:
    epoch = _published(broker, 3)
    value = header.format(epoch=epoch, EPOCH=epoch.upper())
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=value)
    assert stream._cursors == {}
    assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


def test_unreplayable_cursor_falls_back_to_hello_and_resync(
    mode: str, broker: BrokerBackend
) -> None:
    epoch = _published(broker, 3)
    for cursor in (f"{'f' * 16}:1", f"{epoch}:9", f"{epoch}:3"):
        stream = _stream("poll:1", asynchronous=mode == "async", cursor=cursor)
        assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


def test_broker_without_replay_goes_straight_to_hello(mode: str) -> None:
    broker = PlainBroker()
    epoch = _published(broker, 3)
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=f"{epoch}:1")
    assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


def test_multi_topic_stream_ignores_last_event_id(
    mode: str, broker: BrokerBackend
) -> None:
    epoch = _published(broker, 3)
    stream = _stream(
        "poll:1", "poll:2", asynchronous=mode == "async", cursor=f"{epoch}:1"
    )
    assert stream._cursors == {}
    assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


@pytest.mark.parametrize(
    ("step", "expected"),
    [("positions", [2, 3, 4, ("hello", 4), 4]), ("replay", [2, 3, ("hello", 3), 4])],
    ids=["before-snapshot", "during-replay"],
)
def test_live_message_around_replay_is_neither_lost_nor_applied_twice(
    mode: str,
    broker: BrokerBackend,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    expected: list[object],
) -> None:
    epoch = _published(broker, 3)
    _publish_during(broker, step, monkeypatch)
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=f"{epoch}:1")
    trace = _trace(_items(stream, broker, len(expected)))
    assert trace == expected


def test_replayed_messages_pass_through_the_stream_transform(
    mode: str, broker: BrokerBackend
) -> None:
    epoch = _published(broker, 3)
    factory = AsyncRequestFactory() if mode == "async" else RequestFactory()
    request = factory.get("/polls/1/stream/", headers={"Last-Event-ID": f"{epoch}:1"})

    def transform(message: Message):
        if message.seq == 2:
            return None
        return Patches(None).event("poll:update", {"seq": message.seq})

    stream = TopicStream(request, "poll:1", transform=transform, heartbeat_seconds=0.01)
    items = _items(stream, broker, 3)
    assert _trace(items) == [("skip", 2), 3, ("hello", 3)]
    assert items[1].envelope.ops[0].extras["name"] == "poll:update"


def test_revoked_access_during_replay_ends_before_hello(
    mode: str, broker: BrokerBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    epoch = _published(broker, 3)
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=f"{epoch}:1")
    verdicts = iter([Access.GRANTED, Access.GRANTED, Access.TOPIC_DENIED])

    async def acheck() -> Access:
        return next(verdicts)

    monkeypatch.setattr(stream._access, "check", lambda: next(verdicts))
    monkeypatch.setattr(stream._access, "acheck", acheck)
    assert _trace(_items(stream, broker, 3)) == [2, StreamEnd("auth")]


def _event_id(frame: bytes) -> str | None:
    lines = frame.decode().split("\n")
    ids = [line.removeprefix("id: ") for line in lines if line.startswith("id: ")]
    return ids[0] if ids else None


def test_single_topic_sse_frames_carry_the_epoch_and_seq_as_id(
    broker: BrokerBackend,
) -> None:
    epoch = _published(broker, 2)
    request = RequestFactory().get(
        "/polls/1/stream/", headers={"Last-Event-ID": f"{epoch}:1"}
    )
    stream = TopicStream(request, "poll:1", heartbeat_seconds=0.01)
    iterator = stream.streaming_content
    assert next(iterator).startswith(b"retry: ")
    replayed = next(iterator)
    hello = next(iterator)
    broker.publish("poll:1", refresh_envelope())
    live = next(iterator)
    stream._iterator.close()

    assert _event_id(replayed) == f"{epoch}:2"
    assert replayed.startswith(f"id: {epoch}:2\nevent: next-patches\n".encode())
    assert json.loads(replayed.decode().split("data: ", 1)[1])["seq"] == 2
    assert hello.startswith(f"id: {epoch}:2\nevent: next-hello\n".encode())
    assert _event_id(live) == f"{epoch}:3"


def test_multi_topic_sse_frames_carry_no_id(broker: BrokerBackend) -> None:
    request = RequestFactory().get("/polls/stream/")
    stream = TopicStream(request, "poll:1", "poll:2", heartbeat_seconds=0.01)
    iterator = stream.streaming_content
    next(iterator)
    next(iterator)
    broker.publish("poll:2", refresh_envelope())
    frame = next(iterator)
    stream._iterator.close()
    assert b"event: next-patches" in frame
    assert _event_id(frame) is None


def test_async_sse_stream_resumes_from_last_event_id(broker: BrokerBackend) -> None:
    epoch = _published(broker, 3)

    async def scenario() -> list[bytes]:
        request = AsyncRequestFactory().get(
            "/polls/1/stream/", headers={"Last-Event-ID": f"{epoch}:2"}
        )
        stream = TopicStream(request, "poll:1", heartbeat_seconds=0.01)
        iterator = stream.streaming_content.__aiter__()
        frames = [await anext(iterator) for _ in range(3)]
        await stream._iterator.aclose()
        return frames

    retry, replayed, hello = asyncio.run(scenario())
    assert retry.startswith(b"retry: ")
    assert _event_id(replayed) == f"{epoch}:3"
    assert hello.startswith(f"id: {epoch}:3\nevent: next-hello\n".encode())


def _sse_frames(
    stream: TopicStream, count: int, *, live: int = 0, topic: str = "poll:1"
) -> list[bytes]:
    """Read SSE frames after the retry hint, then publish `live` more and read them."""
    broker = broker_manager.get()
    if isinstance(stream._request, ASGIRequest):

        async def collect() -> list[bytes]:
            iterator = stream.streaming_content.__aiter__()
            await anext(iterator)
            frames = [await anext_frame(iterator) for _ in range(count)]
            for _ in range(live):
                await broker.apublish(topic, refresh_envelope())
            frames.extend([await anext_frame(iterator) for _ in range(live)])
            await stream._iterator.aclose()
            return frames

        return asyncio.run(collect())
    iterator = stream.streaming_content
    next(iterator)
    frames = [next_frame(iterator) for _ in range(count)]
    for _ in range(live):
        broker.publish(topic, refresh_envelope())
    frames.extend(next_frame(iterator) for _ in range(live))
    stream._iterator.close()
    return frames


def _skip_all(message: Message) -> None:
    del message


def _filtered(*topics: str, mode: str, cursor: str | None = None) -> TopicStream:
    factory = AsyncRequestFactory() if mode == "async" else RequestFactory()
    headers = {} if cursor is None else {"Last-Event-ID": cursor}
    request = factory.get("/polls/1/stream/", headers=headers)
    return TopicStream(request, *topics, transform=_skip_all, heartbeat_seconds=0.01)


def test_filtered_stream_resumes_past_the_log_from_its_last_skip(
    mode: str,
    make_broker: Callable[..., BrokerBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = make_broker(size=2)
    monkeypatch.setattr(broker_manager, "_backend", broker)
    epoch = _published(broker, 1)
    first = _sse_frames(_filtered("poll:1", mode=mode), 1, live=3)
    assert [_event_id(frame) for frame in first] == [
        f"{epoch}:{seq}" for seq in (1, 2, 3, 4)
    ]
    client = ResumingClient({"poll:1": (epoch, 4)})
    resumed = _sse_frames(
        _filtered("poll:1", mode=mode, cursor=f"{epoch}:4"), 1, live=1
    )
    assert [client.sse(frame) for frame in resumed] == ["next-hello", "next-skip"]
    assert client.gaps == []
    assert client.cursors == {"poll:1": (epoch, 5)}


def test_multi_topic_hello_and_skip_frames_carry_no_id(
    mode: str, broker: BrokerBackend
) -> None:
    _published(broker, 1)
    frames = _sse_frames(_filtered("poll:1", "poll:2", mode=mode), 1, live=1)
    assert [frame.split(b"\n", 1)[0] for frame in frames] == [
        b"event: next-hello",
        b"event: next-skip",
    ]


def test_hello_of_a_topic_never_published_carries_no_id(
    mode: str, broker: BrokerBackend
) -> None:
    (hello,) = _sse_frames(_filtered("poll:1", mode=mode), 1)
    assert hello.startswith(b"event: next-hello\n")


@pytest.mark.parametrize(
    ("step", "events", "dropped"),
    [
        ("positions", ["patches"] * 3 + ["hello", "patches"], [4]),
        ("replay", ["patches"] * 2 + ["hello", "patches"], []),
    ],
    ids=["before-snapshot", "during-replay"],
)
def test_client_applies_each_cursor_once_across_replay_and_live_frames(
    mode: str,
    broker: BrokerBackend,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    events: list[str],
    dropped: list[int],
) -> None:
    epoch = _published(broker, 3)
    _publish_during(broker, step, monkeypatch)
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=f"{epoch}:1")
    client = ResumingClient({"poll:1": (epoch, 1)})
    frames = _sse_frames(stream, len(events))
    assert [client.sse(frame).removeprefix("next-") for frame in frames] == events
    assert client.applied == [("poll:1", epoch, seq) for seq in (2, 3, 4)]
    assert client.dropped == [("poll:1", epoch, seq) for seq in dropped]
    assert client.gaps == []
    assert client.cursors == {"poll:1": (epoch, 4)}
