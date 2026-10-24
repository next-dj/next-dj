import asyncio
import json
import threading
from typing import ClassVar
from unittest.mock import AsyncMock, Mock, create_autospec
from urllib.parse import urlparse

import pytest


pytest.importorskip("channels")

from asgiref.sync import sync_to_async
from asgiref.typing import ASGIApplication, ASGIReceiveCallable, ASGISendCallable, Scope
from channels.security.websocket import OriginValidator
from django.contrib.auth import get_user_model, login
from django.contrib.auth.models import Permission
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from next.contrib.channels import (
    application as channels_application,
    asgi as asgi_module,
    consumers as consumer_module,
    websocket_urlpatterns,
)
from next.contrib.channels.broker import ChannelsLayerBroker
from next.contrib.channels.consumers import RealtimeConsumer
from next.partial import Envelope, JsonPartialProtocolBackend, Patches, StreamMeta
from next.partial.manager import partial_backend_manager
from next.realtime.access import AccessCheck
from next.realtime.backends import BrokerBackend
from next.realtime.errors import BrokerConnectionError
from next.realtime.manager import broker_manager
from next.realtime.messages import Message
from next.realtime.tickets import TicketClaims, TicketGrant, issue_ticket
from tests.support.cases.realtime import (
    MALFORMED_SUBSCRIPTION_CASES,
    RAW_FRAME_CASES,
    TEXT_FRAME_CASES,
    UNUSABLE_REFERENCE_CASES,
    RefusedFrameCase,
    TextFrameCase,
    TicketReferenceCase,
)
from tests.support.codecs import sse_data_text
from tests.support.realtime import (
    ROOM_AUTHORIZER_REF,
    SessionTicket,
    issue_session_ticket,
)
from tests.support.websocket import WebsocketCommunicator


_TOPIC_ALLOWED = True
_TOGGLED_AUTHORIZER_REF = "tests.realtime.test_channels_consumer._authorize_topic"
_AUTHORIZER_CALLS: list[str] = []


def _authorize_topic(_user: object, topic: str) -> bool:
    return _TOPIC_ALLOWED and topic.startswith("room:")


def _counting_authorizer(_user: object, topic: str) -> bool:
    _AUTHORIZER_CALLS.append(topic)
    return True


def _authorize_user_topic(user: object, topic: str) -> bool:
    _ = topic
    return bool(user.is_authenticated and user.has_perm("auth.view_user"))


def _event_transform(message):
    return Patches(None).event("room:update", {"seq": message.seq})


def _skip_message(_message):
    return None


def _failing_transform(_message):
    msg = "transform failed"
    raise RuntimeError(msg)


_BARRIER = threading.Barrier(2, timeout=5)


def _rendezvous_transform(message):
    _BARRIER.wait()
    return Patches(None).event("room:update", {"seq": message.seq})


class _RecordingConsumer(RealtimeConsumer):
    instances: ClassVar[list[RealtimeConsumer]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        _RecordingConsumer.instances.append(self)


@pytest.fixture(autouse=True)
def _forget_consumers():
    _RecordingConsumer.instances.clear()
    _AUTHORIZER_CALLS.clear()
    yield
    _RecordingConsumer.instances.clear()


def _app(session: object, user: object | None = None) -> ASGIApplication:
    application = _RecordingConsumer.as_asgi()

    async def wrapped(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        scope["session"] = session
        if user is not None:
            scope["user"] = user
        await application(scope, receive, send)

    return wrapped


async def _connect(session: object, user: object | None = None):
    communicator = WebsocketCommunicator(_app(session, user), "/_next/ws/")
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _subscribe(communicator: WebsocketCommunicator, grant: TicketGrant):
    await communicator.send_json_to({"type": "sub", "ticket": grant.token})
    hello = await communicator.receive_json_from()
    assert hello["type"] == "hello"
    return hello


async def _next_non_ping(communicator: WebsocketCommunicator) -> dict[str, object]:
    while True:
        frame = await communicator.receive_json_from()
        if frame["type"] != "ping":
            return frame


async def _assert_clean() -> None:
    """Check cleanup inside the loop, before asyncio.run cancels leftovers."""
    for consumer in _RecordingConsumer.instances:
        for task in (
            consumer._task,
            consumer._first_frame_timeout,
            consumer._failure_close,
        ):
            assert task is None or task.done()
    current = asyncio.current_task()
    assert {task for task in asyncio.all_tasks() if task is not current} == set()
    subscribers = getattr(broker_manager.get(), "_subscribers", {})
    assert subscribers == {}


@pytest.mark.parametrize("route", ["/live", "/live/", "/"])
def test_websocket_route_configured_path_matches_itself(route: str) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"WS_PATH": route}}):
        pattern = websocket_urlpatterns()[0].pattern
    assert pattern.match(route.lstrip("/")) is not None


def test_websocket_route_empty_path_raises() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        websocket_urlpatterns("")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("revocation", ["permission", "password", "inactive"])
def test_consumer_user_revoked_while_subscribed_closes_before_delivery(
    revocation: str,
) -> None:
    user = get_user_model().objects.create_user(
        username="subscriber", password="before"
    )
    user.user_permissions.add(Permission.objects.get(codename="view_user"))
    assert user.has_perm("auth.view_user")
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.save()
    grant = issue_ticket(
        request,
        ("room:42",),
        transform_ref=None,
        authorizer_ref="tests.realtime.test_channels_consumer._authorize_user_topic",
    )

    def revoke() -> None:
        if revocation == "permission":
            user.user_permissions.clear()
        elif revocation == "password":
            current = get_user_model().objects.get(pk=user.pk)
            current.set_password("after")
            current.save(update_fields=["password"])
        else:
            get_user_model().objects.filter(pk=user.pk).update(is_active=False)

    async def scenario() -> None:
        communicator = await _connect(request.session, user)
        await _subscribe(communicator, grant)
        await sync_to_async(revoke, thread_sensitive=False)()
        await asyncio.sleep(0.06)
        await broker_manager.get().apublish(
            "room:42", Patches(None).event("room:update").envelope()
        )
        assert await _next_non_ping(communicator) == {"type": "error", "code": "auth"}
        assert (await communicator.receive_output())["code"] in {4401, 4403}
        await communicator.disconnect()
        await _assert_clean()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 0.05}}):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_session_rotation_closes_socket(session_ticket: SessionTicket) -> None:
    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        await sync_to_async(session_ticket.session.cycle_key, thread_sensitive=False)()
        assert await _next_non_ping(communicator) == {"type": "error", "code": "auth"}
        assert await communicator.receive_output() == {
            "type": "websocket.close",
            "code": 4401,
        }
        await communicator.disconnect()
        await _assert_clean()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 0.02}}):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_session_bound_ticket_receives_hello_and_cleans_up() -> None:
    ticket = issue_session_ticket(("room:42", "room:43"))

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        hello = await _subscribe(communicator, ticket.grant)
        assert hello["topics"] == {
            "room:42": {"epoch": "", "seq": 0},
            "room:43": {"epoch": "", "seq": 0},
        }
        assert hello["transports"] == ["ws", "sse"]
        assert hello["heartbeat"] == 7.5
        assert hello["protocol"] == 2
        assert isinstance(hello["server_time"], int)
        await communicator.disconnect()
        await _assert_clean()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 7.5}}):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_json_backend_frame_carries_the_stream_object(
    session_ticket: SessionTicket,
) -> None:
    envelope = Patches(None).refresh(zone="score").envelope()

    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        published = await broker_manager.get().apublish("room:42", envelope)
        frame = await _next_non_ping(communicator)
        assert frame["type"] == "patches"
        data = frame["data"]
        assert isinstance(data, dict)
        meta = StreamMeta(
            server_time=data["server_time"],
            topic="room:42",
            seq=published.seq,
            epoch=published.epoch,
        )
        sse = JsonPartialProtocolBackend().sse_stream_event(envelope, meta)
        assert data == json.loads(sse_data_text(sse))
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", TEXT_FRAME_CASES, ids=lambda case: case.id)
def test_consumer_custom_sse_codec_frame_carries_its_sse_data_text(
    monkeypatch: pytest.MonkeyPatch, session_ticket: SessionTicket, case: TextFrameCase
) -> None:
    frame_of = case.backend.sse_stream_event
    spy = Mock(wraps=frame_of)
    monkeypatch.setattr(case.backend, "sse_stream_event", spy)
    monkeypatch.setattr(partial_backend_manager, "get", lambda: case.backend)
    envelope = Patches.versioned("v7").refresh(zone="score").envelope()

    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        published = await broker_manager.get().apublish("room:42", envelope)
        frame = await _next_non_ping(communicator)
        sent, meta = spy.call_args.args
        assert sent == envelope
        assert (meta.topic, meta.seq, meta.epoch) == (
            "room:42",
            published.seq,
            published.epoch,
        )
        assert frame == {
            "type": "patches",
            "data": sse_data_text(frame_of(envelope, meta)),
        }
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("frame", "data"),
    [
        ("data: a\r\ndata:b\rdata:  c\n\n", "a\nb\n c"),
        ('data: {"t":"\u2028"}\n\n', '{"t":"\u2028"}'),
        ("event: x\n\n", None),
    ],
)
def test_sse_data_event_source_frame_joins_data_lines(
    frame: str, data: str | None
) -> None:
    assert consumer_module._sse_data(frame) == data


def test_consumer_backend_frame_without_data_raises_improperly_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SilentBackend(JsonPartialProtocolBackend):
        def sse_stream_event(self, envelope: Envelope, meta: StreamMeta) -> str:
            _ = envelope, meta
            return "event: x\n\n"

    backend = SilentBackend()
    monkeypatch.setattr(partial_backend_manager, "get", lambda: backend)
    message = Message(
        "room:42", 1, Patches(None).refresh(zone="score").envelope(), 1.0, "e"
    )
    consumer = RealtimeConsumer()
    with pytest.raises(consumer_module.ImproperlyConfigured, match="SSE data field"):
        asyncio.run(consumer._send_message(message))


@pytest.mark.django_db(transaction=True)
def test_consumer_channels_layer_broker_publish_reaches_the_socket() -> None:
    ticket = issue_session_ticket(("room:43",))
    cache.clear()
    with override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        NEXT_FRAMEWORK={
            "REALTIME": {
                "BROKER": {
                    "BACKEND": "next.contrib.channels.ChannelsLayerBroker",
                    "OPTIONS": {"PREFIX": "consumer-test"},
                }
            }
        },
    ):
        broker = broker_manager.get()
        assert isinstance(broker, ChannelsLayerBroker)

        async def scenario() -> None:
            communicator = await _connect(ticket.session)
            await _subscribe(communicator, ticket.grant)
            await sync_to_async(broker.publish, thread_sensitive=False)(
                "room:43", Patches(None).refresh(zone="score").envelope()
            )
            patches = await _next_non_ping(communicator)
            assert patches["type"] == "patches"
            assert patches["data"]["seq"] == 1
            await communicator.disconnect()
            assert not broker._layer.groups.get(broker._group("room:43"), {})

        asyncio.run(scenario())
    cache.clear()


async def _empty_http(scope, receive, send) -> None:
    _ = scope, receive, send


def test_channels_application_foreign_origin_is_refused() -> None:
    async def scenario() -> None:
        app = channels_application(_empty_http, origins=("https://app.test",))
        communicator = WebsocketCommunicator(
            app, "/_next/ws/", headers=[(b"origin", b"https://attacker.test")]
        )
        connected, close_code = await communicator.connect()
        assert not connected
        assert close_code == 1000
        await communicator.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("hosts", [["*"], [], ["/path"], ["."], ["*.example.com"]])
def test_channels_application_unusable_allowed_hosts_raise(hosts: list[str]) -> None:
    with (
        override_settings(ALLOWED_HOSTS=hosts),
        pytest.raises(ValueError, match="ALLOWED_HOSTS"),
    ):
        channels_application(_empty_http)


def test_channels_application_empty_explicit_origin_list_raises() -> None:
    with pytest.raises(ValueError, match="at least one exact WebSocket Origin"):
        channels_application(_empty_http, origins=())


@pytest.mark.parametrize(
    ("origin", "outcome"),
    [
        ("https://example.com", "accepted"),
        ("https://app.example.com", "accepted"),
        ("http://deep.app.example.com:8443", "accepted"),
        ("https://example.org", "rejected"),
        ("https://badexample.com", "rejected"),
        ("http://localhost:8000", "accepted"),
        ("http://localhost", "accepted"),
        ("https://localhost", "accepted"),
        ("http://127.0.0.1:8000", "rejected"),
    ],
)
def test_origin_validator_leading_dot_host_accepts_only_its_own_domain(
    origin: str, outcome: str
) -> None:
    with override_settings(ALLOWED_HOSTS=[".example.com", "LocalHost"]):
        patterns = asgi_module._origins_from_hosts()
    assert patterns == (".example.com", "localhost")
    validator = OriginValidator(_empty_http, patterns)
    assert validator.valid_origin(urlparse(origin)) is (outcome == "accepted")


@pytest.mark.django_db(transaction=True)
def test_consumer_denied_topic_closes_before_subscribing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(globals(), "_TOPIC_ALLOWED", False)
    ticket = issue_session_ticket(authorizer_ref=_TOGGLED_AUTHORIZER_REF)

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await communicator.send_json_to({"type": "sub", "ticket": ticket.grant.token})
        assert await communicator.receive_json_from() == {
            "type": "error",
            "code": "auth",
        }
        assert await communicator.receive_output() == {
            "type": "websocket.close",
            "code": 4403,
        }
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_topic_access_revoked_while_subscribed_closes_4403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(globals(), "_TOPIC_ALLOWED", True)
    ticket = issue_session_ticket(authorizer_ref=_TOGGLED_AUTHORIZER_REF)

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await _subscribe(communicator, ticket.grant)
        monkeypatch.setitem(globals(), "_TOPIC_ALLOWED", False)
        await asyncio.sleep(0.03)
        await broker_manager.get().apublish(
            "room:42", Patches(None).refresh(zone="score").envelope()
        )
        assert await _next_non_ping(communicator) == {"type": "error", "code": "auth"}
        assert await communicator.receive_output() == {
            "type": "websocket.close",
            "code": 4403,
        }
        await communicator.disconnect()
        await _assert_clean()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 0.02}}):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_message_burst_rechecks_access_once_per_heartbeat() -> None:
    ticket = issue_session_ticket(
        authorizer_ref="tests.realtime.test_channels_consumer._counting_authorizer"
    )

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await _subscribe(communicator, ticket.grant)
        for _ in range(5):
            await broker_manager.get().apublish(
                "room:42", Patches(None).refresh(zone="score").envelope()
            )
            assert (await communicator.receive_json_from())["type"] == "patches"
        await communicator.disconnect()
        await _assert_clean()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 60}}):
        asyncio.run(scenario())
    assert _AUTHORIZER_CALLS == ["room:42"]


@pytest.mark.django_db(transaction=True)
def test_consumer_concurrent_transforms_run_on_separate_threads() -> None:
    first = issue_session_ticket(
        transform_ref="tests.realtime.test_channels_consumer._rendezvous_transform"
    )
    second = issue_session_ticket(
        transform_ref="tests.realtime.test_channels_consumer._rendezvous_transform"
    )
    _BARRIER.reset()

    async def scenario() -> None:
        left = await _connect(first.session)
        right = await _connect(second.session)
        await _subscribe(left, first.grant)
        await _subscribe(right, second.grant)
        await broker_manager.get().apublish(
            "room:42", Patches(None).refresh(zone="score").envelope()
        )
        for communicator in (left, right):
            frame = await communicator.receive_output(timeout=5)
            assert json.loads(frame["text"])["type"] == "patches"
        await left.disconnect()
        await right.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


def test_consumer_missing_or_expired_session_is_refused_4401() -> None:
    expired = create_autospec(SessionStore, instance=True)
    expired.session_key = "expired"
    expired.get_expiry_age.return_value = 0

    async def rejected(session: object) -> int:
        communicator = WebsocketCommunicator(_app(session), "/_next/ws/")
        connected, close_code = await communicator.connect()
        assert not connected
        await communicator.disconnect()
        return close_code

    async def scenario() -> None:
        assert await rejected(None) == 4401
        assert await rejected(expired) == 4401
        await _assert_clean()

    asyncio.run(scenario())
    expired.get_expiry_age.assert_called_once_with()


async def _send_refused_frame(
    session: SessionStore, case: RefusedFrameCase
) -> WebsocketCommunicator:
    communicator = await _connect(session)
    await communicator.send_input({"type": "websocket.receive", **case.receive})
    assert await communicator.receive_json_from() == {
        "type": "error",
        "code": case.error,
    }
    assert (await communicator.receive_output())["code"] == case.close_code
    return communicator


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", MALFORMED_SUBSCRIPTION_CASES, ids=lambda case: case.id)
def test_consumer_malformed_subscription_frame_closes_with_protocol_error(
    session_ticket: SessionTicket, case: RefusedFrameCase
) -> None:
    async def scenario() -> None:
        communicator = await _send_refused_frame(session_ticket.session, case)
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", RAW_FRAME_CASES, ids=lambda case: case.id)
def test_consumer_raw_malformed_frame_closes_without_orphans(
    session_ticket: SessionTicket, case: RefusedFrameCase
) -> None:
    async def scenario() -> None:
        communicator = await _send_refused_frame(session_ticket.session, case)
        await communicator.send_json_to({"type": "sub", "ticket": "late"})
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_malformed_frame_after_subscribing_stops_the_stream(
    session_ticket: SessionTicket,
) -> None:
    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        await communicator.send_to(text_data="{")
        assert await _next_non_ping(communicator) == {
            "type": "error",
            "code": "protocol",
        }
        assert (await communicator.receive_output())["code"] == 4400
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_server_cancelling_the_app_cancels_its_children(
    session_ticket: SessionTicket,
) -> None:
    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        communicator.future.cancel()
        with pytest.raises(asyncio.CancelledError):
            await communicator.future
        consumer = _RecordingConsumer.instances[0]
        assert consumer._task is not None
        assert consumer._task.done()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "cursor",
    [{"room:42": 0, "room:other": 12}, {"room:42": {"epoch": "a1", "seq": 3}}, None],
)
def test_valid_cursor_well_formed_sequence_cursor_is_accepted(cursor: object) -> None:
    assert consumer_module._valid_cursor(cursor)


@pytest.mark.parametrize(
    "cursor",
    [
        {"room:42": {"epoch": 1, "seq": 3}},
        {"room:42": {"epoch": "a", "seq": True}},
        {"room:42": {"epoch": "a", "seq": 1, "x": 1}},
        {1: 0},
        [],
    ],
)
def test_valid_cursor_malformed_sequence_cursor_is_rejected(cursor: object) -> None:
    assert not consumer_module._valid_cursor(cursor)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", UNUSABLE_REFERENCE_CASES, ids=lambda case: case.id)
def test_consumer_unusable_ticket_reference_closes_with_auth_error(
    case: TicketReferenceCase,
) -> None:
    ticket = issue_session_ticket(
        transform_ref=case.transform_ref, authorizer_ref=case.authorizer_ref
    )

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await communicator.send_json_to({"type": "sub", "ticket": ticket.grant.token})
        assert await communicator.receive_json_from() == {
            "type": "error",
            "code": case.error,
        }
        assert (await communicator.receive_output())["code"] == case.close_code
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_control_frames_accept_pong_and_refuse_ping(
    session_ticket: SessionTicket,
) -> None:
    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        await communicator.send_json_to({"type": "pong"})
        await communicator.send_json_to({"type": "ping"})
        assert await communicator.receive_json_from() == {
            "type": "error",
            "code": "unsupported",
        }
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_importable_transform_rewrites_the_patches() -> None:
    ticket = issue_session_ticket(
        transform_ref="tests.realtime.test_channels_consumer._event_transform"
    )

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await _subscribe(communicator, ticket.grant)
        await broker_manager.get().apublish(
            "room:42", Patches(None).refresh(zone="score").envelope()
        )
        frame = await _next_non_ping(communicator)
        assert frame["type"] == "patches"
        assert frame["data"]["ops"][0]["name"] == "room:update"
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_transform_skipping_a_message_sends_skip_with_epoch() -> None:
    ticket = issue_session_ticket(
        transform_ref="tests.realtime.test_channels_consumer._skip_message"
    )

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await _subscribe(communicator, ticket.grant)
        published = await broker_manager.get().apublish(
            "room:42", Patches(None).refresh(zone="score").envelope()
        )
        assert await _next_non_ping(communicator) == {
            "type": "skip",
            "topic": "room:42",
            "epoch": published.epoch,
            "seq": published.seq,
        }
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_consumer_failing_transform_logs_and_closes_1011(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ticket = issue_session_ticket(
        transform_ref="tests.realtime.test_channels_consumer._failing_transform"
    )

    async def scenario() -> None:
        communicator = await _connect(ticket.session)
        await _subscribe(communicator, ticket.grant)
        await broker_manager.get().apublish(
            "room:42", Patches(None).refresh(zone="score").envelope()
        )
        assert await communicator.receive_output(timeout=5) == {
            "type": "websocket.close",
            "code": 1011,
        }
        await communicator.disconnect()
        await _assert_clean()

    asyncio.run(scenario())
    assert "Realtime WebSocket stream failed" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_consumer_idle_subscription_emits_ping_at_the_heartbeat(
    session_ticket: SessionTicket,
) -> None:
    async def scenario() -> None:
        communicator = await _connect(session_ticket.session)
        await _subscribe(communicator, session_ticket.grant)
        ping = await communicator.receive_json_from()
        assert ping["type"] == "ping"
        assert isinstance(ping["server_time"], int)
        await communicator.disconnect()
        await _assert_clean()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 0.001}}):
        asyncio.run(scenario())


def test_consumer_disconnect_without_children_leaves_no_tasks() -> None:
    consumer = RealtimeConsumer()

    async def scenario() -> None:
        await consumer.disconnect(1000)
        assert consumer._stopped is True
        assert consumer._task is None
        await _assert_clean()

    asyncio.run(scenario())


def _serve_against(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> RealtimeConsumer:
    broker = create_autospec(BrokerBackend, instance=True)
    broker.asubscribe.side_effect = failure
    monkeypatch.setattr(
        broker_manager,
        "aget",
        create_autospec(broker_manager.aget, return_value=broker),
    )
    consumer = RealtimeConsumer()
    consumer.close = create_autospec(consumer.close)
    access = AccessCheck(
        ("room:42",), authorizer=None, session_key=None, user=None, interval=1
    )
    claims = TicketClaims(("room:42",), None, ROOM_AUTHORIZER_REF)
    asyncio.run(consumer._serve(claims, None, access))
    broker.asubscribe.assert_called_once_with(("room:42",), timeout=consumer._heartbeat)
    return consumer


@pytest.mark.parametrize("failure", [ConnectionError, OSError])
def test_consumer_broker_connection_failure_closes_1011(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    consumer = _serve_against(monkeypatch, failure())
    consumer.close.assert_awaited_once_with(code=1011)


def test_consumer_finished_stream_after_stopping_schedules_no_close() -> None:
    consumer = RealtimeConsumer()

    async def scenario() -> None:
        async def boom() -> None:
            msg = "late"
            raise RuntimeError(msg)

        async def fine() -> None:
            return

        failed = asyncio.ensure_future(boom())
        finished = asyncio.ensure_future(fine())
        await asyncio.wait({failed, finished})
        consumer._stopped = True
        consumer._serve_finished(failed)
        assert consumer._failure_close is None
        consumer._stopped = False
        consumer._serve_finished(finished)
        assert consumer._failure_close is None
        consumer.close = create_autospec(consumer.close)
        consumer._serve_finished(failed)
        assert consumer._failure_close is not None
        await consumer._failure_close
        consumer.close.assert_awaited_once_with(code=1011)

    asyncio.run(scenario())


def test_consumer_no_subscription_before_timeout_closes_with_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consumer_module, "_SUB_TIMEOUT", 0)
    consumer = RealtimeConsumer()
    consumer.send_json = create_autospec(consumer.send_json)
    consumer.close = create_autospec(consumer.close)
    asyncio.run(consumer._close_if_unsubscribed())
    consumer.send_json.assert_awaited_once_with({"type": "error", "code": "protocol"})
    consumer.close.assert_awaited_once_with(code=4400)


def test_consumer_first_frame_timeout_is_noop_after_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(consumer_module, "_SUB_TIMEOUT", 0)
    consumer = RealtimeConsumer()
    consumer._subscribed = True
    consumer.send_json = AsyncMock(spec=RealtimeConsumer.send_json)
    asyncio.run(consumer._close_if_unsubscribed())
    consumer.send_json.assert_not_awaited()


def test_consumer_broker_dropping_the_subscription_closes_1011(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _serve_against(monkeypatch, BrokerConnectionError("reconnected"))
    consumer.close.assert_awaited_once_with(code=1011)


@pytest.mark.django_db(transaction=True)
def test_consumer_subscribe_without_first_frame_timer_starts_the_stream(
    session_ticket: SessionTicket,
) -> None:
    consumer = _RecordingConsumer()
    consumer.scope = {"session": session_ticket.session}
    consumer._session_key = session_ticket.session.session_key
    consumer._session_expires_at = float("inf")
    sent = asyncio.Event()
    consumer.send_json = create_autospec(
        consumer.send_json, side_effect=lambda *_args, **_kwargs: sent.set()
    )

    async def scenario() -> None:
        await consumer._subscribe({"type": "sub", "ticket": session_ticket.grant.token})
        assert consumer._subscribed is True
        await asyncio.wait_for(sent.wait(), timeout=5)
        await consumer.disconnect(1000)
        await _assert_clean()

    asyncio.run(scenario())
    assert consumer.send_json.await_args_list[0].args[0]["type"] == "hello"
