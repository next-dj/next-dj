from dataclasses import dataclass
from types import MappingProxyType

import pytest
from django.core.signals import request_finished
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from next.partial.headers import STREAM_ACCEPT
from next.partial.stream import (
    PROTOCOL,
    StreamHello,
    StreamPatch,
    current_streamable,
    owned,
    stream_hello,
    streamable,
)
from next.realtime.messages import Position
from next.testing import capture_signals


@dataclass(frozen=True)
class BrokerPosition:
    """A broker's own position type, read through its two attributes only."""

    epoch: str
    seq: int
    extra: str = "ignored"


@pytest.mark.parametrize("position_type", [Position, BrokerPosition])
@pytest.mark.parametrize("transports", [("sse",), ("ws", "sse")])
def test_stream_hello_reads_each_topic_position_in_topic_order(
    position_type: type, transports: tuple[str, ...]
) -> None:
    positions = {
        "poll:1": position_type("e1", 4),
        "poll:2": position_type("", 0),
        "poll:3": position_type("e3", 9),
    }
    hello = stream_hello(
        ["poll:2", "poll:1"], positions, server_time=1500, transports=transports
    )
    assert hello == StreamHello(
        server_time=1500,
        topics={"poll:2": ("", 0), "poll:1": ("e1", 4)},
        transports=transports,
    )
    assert list(hello.topics) == ["poll:2", "poll:1"]
    assert isinstance(hello.topics, MappingProxyType)
    assert hello.as_dict() == {
        "protocol": 2,
        "server_time": 1500,
        "topics": {
            "poll:2": {"epoch": "", "seq": 0},
            "poll:1": {"epoch": "e1", "seq": 4},
        },
        "transports": list(transports),
    }


def test_stream_hello_consumes_a_one_shot_topic_iterable() -> None:
    hello = stream_hello(
        iter(("poll:1",)),
        {"poll:1": Position("e1", 1)},
        server_time=0,
        transports=("sse",),
    )
    assert dict(hello.topics) == {"poll:1": ("e1", 1)}


def test_stream_hello_topic_without_a_position_raises() -> None:
    with pytest.raises(KeyError, match="poll:9"):
        stream_hello(["poll:9"], {}, server_time=0, transports=("sse",))


def test_a_streamed_hello_names_only_the_protocol() -> None:
    hello = StreamHello(server_time=9, topics={"t": ("e", 1)}, streamed=True)
    assert hello.as_dict() == {"protocol": PROTOCOL, "streamed": True}


def test_a_realtime_hello_keeps_protocol_two() -> None:
    assert StreamHello(server_time=0, topics={}).as_dict()["protocol"] == 2


def test_a_patch_frame_copies_its_data() -> None:
    data = {"z": "b-00000000-0", "s": "ready"}
    patch = StreamPatch(data)
    body = patch.as_dict()
    body["s"] = "error"
    assert patch.as_dict() == data


def test_a_streaming_get_opens_a_block_for_its_render() -> None:
    request = RequestFactory().get("/", HTTP_ACCEPT=STREAM_ACCEPT)
    assert current_streamable() is None
    with streamable(request) as block:
        assert block is not None
        assert current_streamable() is block
        assert (block.asked, block.deferred) == (False, None)
    assert current_streamable() is None


@pytest.mark.parametrize(
    ("method", "accept"),
    [("post", STREAM_ACCEPT), ("head", STREAM_ACCEPT), ("get", "application/json")],
)
def test_a_request_that_takes_no_stream_opens_no_block(
    method: str, accept: str
) -> None:
    request = getattr(RequestFactory(), method)("/", HTTP_ACCEPT=accept)
    with streamable(request) as block:
        assert block is None
        assert current_streamable() is None


class _Held(StreamingHttpResponse):
    """A stream that records being discarded."""

    discarded = False

    def discard(self) -> None:
        self.discarded = True


def test_a_failure_after_a_stream_left_its_view_discards_it_unclosed() -> None:
    response = _Held(iter(()))
    with (
        capture_signals(request_finished) as recorder,
        pytest.raises(KeyboardInterrupt),
        owned(response),
    ):
        raise KeyboardInterrupt
    assert response.discarded is True
    assert response.closed is False
    assert recorder.events == []


def test_a_failure_after_a_plain_response_leaves_it_alone() -> None:
    response = HttpResponse("body")
    with pytest.raises(RuntimeError), owned(response):
        raise RuntimeError
    assert response.closed is False


def test_a_block_that_passes_keeps_the_stream() -> None:
    response = _Held(iter(()))
    with owned(response):
        pass
    assert response.discarded is False
