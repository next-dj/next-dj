import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from next.partial import keys


PATCH_EVENT = "next-patches"
LOAD_EVENT = "load"


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One dispatched Server-Sent Event, its data lines already joined."""

    name: str
    data: str


@dataclass(slots=True)
class SseParser:
    """Turn decoded stream lines into events the way an EventSource does.

    Data lines join with a newline, one leading space after a colon is dropped.
    """

    _name: str = ""
    _data: list[str] = field(default_factory=list)

    def feed(self, line: str) -> SseEvent | None:
        """Consume one line and return the event a blank line completes."""
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._name = value
        elif name == "data":
            self._data.append(value)
        return None

    def _dispatch(self) -> SseEvent | None:
        """Emit the buffered event, or nothing for a frame without data."""
        name, data = self._name or "message", self._data
        self._name, self._data = "", []
        if not data:
            return None
        return SseEvent(name, "\n".join(data))


@dataclass(frozen=True, slots=True)
class Delivery:
    """The fields of one patch frame that gap detection and latency need."""

    topic: str
    epoch: str
    seq: int
    server_time: float
    sent: float


def hello_positions(frame: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    """Return the `(epoch, seq)` start position per topic of a hello frame."""
    topics = frame["topics"]
    return {
        topic: (str(position[keys.EPOCH]), int(position[keys.SEQ]))
        for topic, position in topics.items()
    }


def patch_payload(data: object) -> Mapping[str, Any]:
    """Accept a WS `patches` data as a JSON object, or a string to decode first.

    The JSON codec sends an object, a custom codec sends its SSE data text.
    """
    decoded = json.loads(data) if isinstance(data, str) else data
    if not isinstance(decoded, Mapping):
        msg = "A patch frame must carry a JSON object."
        raise TypeError(msg)
    return decoded


def delivery(frame: Mapping[str, Any]) -> Delivery:
    """Read stream metadata and the publisher's send time out of one patch frame."""
    sent = next(
        (
            op["detail"]["sent"]
            for op in frame[keys.OPS]
            if op.get(keys.OP) == "event" and op.get("name") == LOAD_EVENT
        ),
        None,
    )
    if sent is None:
        msg = "The patch frame carries no load event."
        raise KeyError(msg)
    return Delivery(
        topic=str(frame[keys.TOPIC]),
        epoch=str(frame[keys.EPOCH]),
        seq=int(frame[keys.SEQ]),
        server_time=float(frame[keys.SERVER_TIME]) / 1000,
        sent=float(sent),
    )
