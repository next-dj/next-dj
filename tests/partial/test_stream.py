from dataclasses import dataclass
from types import MappingProxyType

import pytest

from next.partial.stream import StreamHello, stream_hello
from next.realtime.messages import Position


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
