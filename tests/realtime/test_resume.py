import asyncio
import secrets
from collections.abc import Callable

import pytest
from django.core.handlers.asgi import ASGIRequest
from django.test import AsyncRequestFactory, RequestFactory

from next.partial.stream import StreamHello, StreamSkip
from next.realtime import Message, TopicStream
from next.realtime.backends import BrokerBackend
from next.realtime.manager import broker_manager
from next.testing import SseEvent, SseReader
from tests.support.realtime import (
    ResumingClient,
    publish_during,
    published,
    refresh_envelope,
)


_MAX_PULLS = 50
_DONE = object()


@pytest.fixture()
def broker(replaying_broker: BrokerBackend) -> BrokerBackend:
    return replaying_broker


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
    mode: str, broker: BrokerBackend, header: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets, "token_hex", lambda nbytes: "a1" * nbytes)
    epoch = published(broker, 3, "poll:1")
    assert epoch.upper() != epoch
    value = header.format(epoch=epoch, EPOCH=epoch.upper())
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=value)
    assert stream._cursors == {}
    assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


def test_unreplayable_cursor_falls_back_to_hello_and_resync(
    mode: str, broker: BrokerBackend
) -> None:
    epoch = published(broker, 3, "poll:1")
    for cursor in (f"{'f' * 16}:1", f"{epoch}:9", f"{epoch}:3"):
        stream = _stream("poll:1", asynchronous=mode == "async", cursor=cursor)
        assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


def test_multi_topic_stream_ignores_last_event_id(
    mode: str, broker: BrokerBackend
) -> None:
    epoch = published(broker, 3, "poll:1")
    stream = _stream(
        "poll:1", "poll:2", asynchronous=mode == "async", cursor=f"{epoch}:1"
    )
    assert stream._cursors == {}
    assert _trace(_items(stream, broker, 1)) == [("hello", 3)]


def test_single_topic_sse_frames_carry_the_epoch_and_seq_as_id(
    broker: BrokerBackend,
) -> None:
    epoch = published(broker, 2, "poll:1")
    request = RequestFactory().get(
        "/polls/1/stream/", headers={"Last-Event-ID": f"{epoch}:1"}
    )
    stream = TopicStream(request, "poll:1", heartbeat_seconds=0.01)
    with SseReader(stream) as events:
        replayed = next(events)
        hello = next(events)
        broker.publish("poll:1", refresh_envelope())
        live = next(events)

    assert (replayed.id, replayed.event) == (f"{epoch}:2", "next-patches")
    assert replayed.json()["seq"] == 2
    assert (hello.id, hello.event) == (f"{epoch}:2", "next-hello")
    assert live.id == f"{epoch}:3"


def test_multi_topic_sse_frames_carry_no_id(broker: BrokerBackend) -> None:
    request = RequestFactory().get("/polls/stream/")
    stream = TopicStream(request, "poll:1", "poll:2", heartbeat_seconds=0.01)
    with SseReader(stream) as events:
        assert next(events).event == "next-hello"
        broker.publish("poll:2", refresh_envelope())
        frame = next(events)
    assert frame.event == "next-patches"
    assert frame.id is None


def test_async_sse_stream_resumes_from_last_event_id(broker: BrokerBackend) -> None:
    epoch = published(broker, 3, "poll:1")

    async def scenario() -> tuple[int | None, list[SseEvent]]:
        request = AsyncRequestFactory().get(
            "/polls/1/stream/", headers={"Last-Event-ID": f"{epoch}:2"}
        )
        stream = TopicStream(request, "poll:1", heartbeat_seconds=0.01)
        async with SseReader(stream) as events:
            frames = [await anext(events), await anext(events)]
            return events.retry, frames

    retry, (replayed, hello) = asyncio.run(scenario())
    assert retry is not None
    assert replayed.id == f"{epoch}:3"
    assert (hello.id, hello.event) == (f"{epoch}:3", "next-hello")


def _sse_frames(
    stream: TopicStream, count: int, *, live: int = 0, topic: str = "poll:1"
) -> list[SseEvent]:
    """Read SSE events, then publish `live` more and read them."""
    broker = broker_manager.get()
    if isinstance(stream._request, ASGIRequest):

        async def collect() -> list[SseEvent]:
            async with SseReader(stream) as events:
                frames = [await anext(events) for _ in range(count)]
                for _ in range(live):
                    await broker.apublish(topic, refresh_envelope())
                frames.extend([await anext(events) for _ in range(live)])
            return frames

        return asyncio.run(collect())
    with SseReader(stream) as events:
        frames = [next(events) for _ in range(count)]
        for _ in range(live):
            broker.publish(topic, refresh_envelope())
        frames.extend(next(events) for _ in range(live))
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
    epoch = published(broker, 1, "poll:1")
    first = _sse_frames(_filtered("poll:1", mode=mode), 1, live=3)
    assert [frame.id for frame in first] == [f"{epoch}:{seq}" for seq in (1, 2, 3, 4)]
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
    published(broker, 1, "poll:1")
    frames = _sse_frames(_filtered("poll:1", "poll:2", mode=mode), 1, live=1)
    assert [(frame.event, frame.id) for frame in frames] == [
        ("next-hello", None),
        ("next-skip", None),
    ]


def test_hello_of_a_topic_never_published_carries_no_id(
    mode: str, broker: BrokerBackend
) -> None:
    (hello,) = _sse_frames(_filtered("poll:1", mode=mode), 1)
    assert (hello.event, hello.id) == ("next-hello", None)


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
    epoch = published(broker, 3, "poll:1")
    publish_during(broker, step, monkeypatch, "poll:1")
    stream = _stream("poll:1", asynchronous=mode == "async", cursor=f"{epoch}:1")
    client = ResumingClient({"poll:1": (epoch, 1)})
    frames = _sse_frames(stream, len(events))
    assert [client.sse(frame).removeprefix("next-") for frame in frames] == events
    assert client.applied == [("poll:1", epoch, seq) for seq in (2, 3, 4)]
    assert client.dropped == [("poll:1", epoch, seq) for seq in dropped]
    assert client.gaps == []
    assert client.cursors == {"poll:1": (epoch, 4)}
