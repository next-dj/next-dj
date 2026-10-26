import asyncio
import logging
import threading
from collections.abc import Iterator

import pytest
from django.db import transaction
from django.test import RequestFactory, TestCase

from next.partial import Envelope, Patch, Patches
from next.partial.headers import REQUEST_ID
from next.realtime import MemoryBroker, TopicPayloadError, apublish, delivery, publish
from next.realtime.messages import Message
from next.realtime.roster import announce
from next.realtime.signals import message_published
from next.testing import SignalRecorder


def _envelope(*patches: Patch, **metadata) -> Envelope:
    return Envelope(version="v1", ops=patches, **metadata)


def test_publish_safe_event_delivers_and_signals_message(
    memory_broker: MemoryBroker,
) -> None:
    with memory_broker.subscribe(["room:1"], timeout=0.01) as messages:
        with SignalRecorder(message_published) as recorder:
            returned = publish(
                "room:1",
                Patches(None).event("room:update", {"count": 2}),
                on_commit=False,
            )
        message = next(messages)
    event = recorder.events_for(message_published)[0]
    assert event.kwargs["topic"] == "room:1"
    assert event.kwargs["message"] == message
    assert message is not None
    assert message.seq == 1
    assert returned == message


def test_publish_partial_request_copies_request_id(memory_broker: MemoryBroker) -> None:
    request = RequestFactory().post(
        "/vote/",
        HTTP_X_NEXT_REQUEST="1",
        **{f"HTTP_{REQUEST_ID.upper().replace('-', '_')}": "request-7"},
    )
    with memory_broker.subscribe(["poll:1"], timeout=0.01) as messages:
        publish("poll:1", Patches(request).refresh(zone="results"), on_commit=False)
        message = next(messages)
    assert message is not None
    assert message.envelope.request_id == "request-7"


def test_publish_request_without_id_keeps_explicit_echo_only(
    memory_broker: MemoryBroker,
) -> None:
    request = RequestFactory().get("/")
    with memory_broker.subscribe(["poll:1"], timeout=0.01) as messages:
        publish("poll:1", Patches(request).refresh(zone="results"), on_commit=False)
        publish(
            "poll:1",
            Patches(request, echo_of="explicit").refresh(zone="results"),
            on_commit=False,
        )
        first = next(messages)
        second = next(messages)
    assert first is not None
    assert first.envelope.request_id is None
    assert second is not None
    assert second.envelope.request_id == "explicit"


@pytest.mark.django_db()
def test_publish_in_transaction_delivers_after_commit(
    memory_broker: MemoryBroker,
) -> None:
    with (
        SignalRecorder(message_published) as recorder,
        TestCase.captureOnCommitCallbacks(execute=True),
        transaction.atomic(),
    ):
        assert publish("poll:commit", Patches(None).refresh(zone="results")) is None
        assert recorder.events_for(message_published) == []
    assert len(recorder.events_for(message_published)) == 1
    assert memory_broker.positions(["poll:commit"])["poll:commit"].seq == 1


def test_publish_on_commit_registers_on_requested_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = {}

    def capture(callback, using=None, **kwargs):
        registered.update(callback=callback, using=using, options=kwargs)

    monkeypatch.setattr(delivery.transaction, "on_commit", capture)
    publish("poll:db-alias", Patches(None).refresh(zone="results"), using="analytics")
    assert registered["using"] == "analytics"
    assert registered["options"] == {"robust": True}
    assert registered["callback"].__qualname__ == "publish('poll:db-alias')"


@pytest.mark.django_db()
def test_publish_rolled_back_transaction_drops_message(
    memory_broker: MemoryBroker,
) -> None:
    with SignalRecorder(message_published) as recorder, transaction.atomic():
        publish("poll:rollback", Patches(None).refresh(zone="results"))
        transaction.set_rollback(True)
    assert recorder.events_for(message_published) == []
    assert memory_broker.positions(["poll:rollback"])["poll:rollback"].seq == 0


def test_apublish_returns_numbered_message_and_signals(
    memory_broker: MemoryBroker,
) -> None:
    async def scenario() -> None:
        with SignalRecorder(message_published) as recorder:
            message = await apublish(
                "poll:async", Patches(None).refresh(zone="results")
            )
        assert message.seq == 1
        assert recorder.events_for(message_published)[0].kwargs["message"] == message

    asyncio.run(scenario())
    assert memory_broker.positions(["poll:async"])["poll:async"].seq == 1


def test_apublish_async_receiver_is_awaited_before_return(
    memory_broker: MemoryBroker,
) -> None:
    received = []

    async def receiver(sender, **kwargs):
        await asyncio.sleep(0)
        received.append(kwargs["message"])

    async def scenario() -> None:
        message = await apublish("poll:async", Patches(None).refresh(zone="results"))
        assert received == [message]

    message_published.connect(receiver, sender=MemoryBroker)
    try:
        asyncio.run(scenario())
    finally:
        message_published.disconnect(receiver, sender=MemoryBroker)


def test_publish_deferred_payload_mutation_is_not_delivered(
    memory_broker: MemoryBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    callbacks = []
    monkeypatch.setattr(
        delivery.transaction,
        "on_commit",
        lambda callback, **kwargs: callbacks.append(callback),
    )
    detail = {"choices": []}
    ops = [Patch(op="event", extras={"name": "poll:update", "detail": detail})]
    envelope = Envelope(version="v1", ops=ops)
    publish("poll:snapshot", envelope)
    detail["choices"].append("late")
    ops.append(Patch(op="morph", html="<p>private</p>"))
    with memory_broker.subscribe(["poll:snapshot"], timeout=0.01) as messages:
        callbacks[0]()
        message = next(messages)
    assert message is not None
    assert [patch.op for patch in message.envelope.ops] == ["event"]
    assert message.envelope.ops[0].extras["detail"] == {"choices": []}


@pytest.mark.parametrize(
    "envelope",
    [
        _envelope(Patch(op="morph", html="<p>private</p>")),
        _envelope(Patch(op="custom")),
        _envelope(Patch(op="refresh", html="<p>private</p>")),
        _envelope(assets=(object(),)),
        _envelope(form=object()),
        _envelope(csrf={"token": "secret"}),
        _envelope(Patch(op="event", extras={"detail": {"bad": object()}})),
    ],
)
def test_publish_unsafe_envelope_raises(envelope: Envelope) -> None:
    with pytest.raises(TopicPayloadError):
        publish("room:1", envelope, on_commit=False)


def test_publish_non_envelope_raises() -> None:
    with pytest.raises(TypeError, match="Patches builder or partial Envelope"):
        publish("room:1", object(), on_commit=False)


def test_publish_broker_failure_raises_without_signal(
    memory_broker: MemoryBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_publish(topic: str, envelope: Envelope):
        raise RuntimeError

    monkeypatch.setattr(memory_broker, "publish", fail_publish)
    with SignalRecorder(message_published) as recorder, pytest.raises(RuntimeError):
        publish("room:1", Patches(None).refresh(zone="results"), on_commit=False)
    assert recorder.events_for(message_published) == []


@pytest.mark.django_db()
def test_publish_broker_failure_after_commit_is_logged(
    memory_broker: MemoryBroker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_publish(topic: str, envelope: Envelope):
        raise ConnectionError(topic)

    monkeypatch.setattr(memory_broker, "publish", fail_publish)
    with (
        caplog.at_level(logging.ERROR, logger="django.db.backends.base"),
        TestCase.captureOnCommitCallbacks(execute=True),
        transaction.atomic(),
    ):
        publish("poll:down", Patches(None).refresh(zone="results"))
    assert "poll:down" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_publish_broker_failure_in_autocommit_is_logged(
    memory_broker: MemoryBroker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_publish(topic: str, envelope: Envelope):
        raise ConnectionError(topic)

    monkeypatch.setattr(memory_broker, "publish", fail_publish)
    with caplog.at_level(logging.ERROR, logger="django.db.backends.base"):
        publish("poll:autocommit", Patches(None).refresh(zone="results"))
    assert "poll:autocommit" in caplog.text


def _broken_receiver(**kwargs: object) -> None:
    msg = "receiver failed"
    raise ValueError(msg)


@pytest.fixture()
def failing_receiver() -> Iterator[None]:
    message_published.connect(_broken_receiver, weak=False)
    yield
    message_published.disconnect(_broken_receiver)


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "next.realtime.signals"
    ]


@pytest.mark.usefixtures("failing_receiver")
def test_failing_receiver_leaves_publish_delivered_and_logged(
    memory_broker: MemoryBroker, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        memory_broker.subscribe(["room:1"], timeout=0.01) as messages,
        caplog.at_level("ERROR", "next.realtime.signals"),
    ):
        returned = publish("room:1", Patches(None).event("ping"), on_commit=False)
        assert next(messages) == returned
    (logged,) = _logged(caplog)
    assert "message_published" in logged
    assert "topic room:1" in logged


@pytest.mark.usefixtures("failing_receiver")
def test_failing_receiver_leaves_apublish_returning_and_logged(
    memory_broker: MemoryBroker, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("ERROR", "next.realtime.signals"):
        message = asyncio.run(apublish("room:2", Patches(None).event("ping")))
    assert message.seq == 1
    (logged,) = _logged(caplog)
    assert "message_published" in logged
    assert "topic room:2" in logged


def test_numbered_message_is_not_ephemeral(memory_broker: MemoryBroker) -> None:
    with SignalRecorder(message_published) as recorder:
        publish("room:3", Patches(None).event("ping"), on_commit=False)
        asyncio.run(apublish("room:3", Patches(None).event("ping")))
    assert [event.kwargs["ephemeral"] for event in recorder] == [False, False]


def test_presence_snapshot_is_ephemeral(memory_broker: MemoryBroker) -> None:
    with SignalRecorder(message_published) as recorder:
        announce(memory_broker, "room:4")
    (event,) = recorder
    assert event.kwargs["ephemeral"] is True
    assert event.kwargs["message"].numbered is False


def _message() -> Message:
    return Message("room:5", 1, Patches(None).refresh(zone="feed").envelope(), 0.0)


async def _async_event(message: Message) -> Patches:
    await asyncio.sleep(0)
    return Patches(None).event("room:seen", {"seq": message.seq})


async def _async_skip(_message: Message) -> None:
    await asyncio.sleep(0)


async def _async_failing(_message: Message) -> None:
    msg = "async transform failed"
    raise RuntimeError(msg)


class _AsyncCallable:
    async def __call__(self, message: Message) -> Patches:
        return await _async_event(message)


@pytest.mark.parametrize("transform", [_async_event, _AsyncCallable()])
def test_an_async_transform_is_awaited_on_the_loop(transform: object) -> None:
    threads: list[threading.Thread] = []

    async def scenario() -> Message | None:
        threads.append(threading.current_thread())
        return await delivery.atransformed(_message(), transform)

    result = asyncio.run(scenario())
    assert result is not None
    assert result.envelope.ops[0].extras["name"] == "room:seen"
    assert threads == [threading.main_thread()]


def test_an_async_transform_that_returns_none_skips_the_message() -> None:
    assert asyncio.run(delivery.atransformed(_message(), _async_skip)) is None


def test_a_failing_async_transform_skips_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("ERROR", "next.realtime.delivery"):
        assert asyncio.run(delivery.atransformed(_message(), _async_failing)) is None
    assert "Realtime transform failed on room:5 seq 1" in caplog.text


def test_a_sync_stream_runs_an_async_transform_to_completion() -> None:
    result = delivery.transformed(_message(), _async_event)
    assert result is not None
    assert result.envelope.ops[0].extras["name"] == "room:seen"
