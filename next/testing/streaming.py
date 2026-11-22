"""Read a streamed page or partial response the way a browser applies it."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, Final

from next.compression.manager import response_decoder
from next.compression.streams import EVENT_STREAM, media_type
from next.partial import keys
from next.partial.envelope import Envelope
from next.partial.headers import REQUEST_FLAG, STREAM_ACCEPT
from next.partial.stream import END_EVENT, HELLO_EVENT
from next.static.runtime import SCRIPT_TEXT
from next.suspense.partial import PartialStream
from next.suspense.policy import streaming
from next.suspense.response import StreamingPageResponse
from next.testing.boundaries import HeldFailures, release_gates
from next.testing.realtime import sse_events


if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.http.response import HttpResponseBase, StreamingHttpResponse
    from django.test import AsyncClient, Client


_PUSH: Final = re.compile(r"\$nx(?:\|\|=\[\]\))?\.push\(")
_PAD: Final = re.compile(r"(?:<!--[ A-Za-z0-9._]*-->)+\Z")
_JSON: Final = json.JSONDecoder()
_OPENING: Final = r'<(?P<tag>[A-Za-z][A-Za-z0-9-]*)\b[^>]*\sid="{ident}"[^>]*>'
_KEYED: Final = re.compile(
    r'id="(?P<id>b-[^"]+)" data-next-boundary="[^"]+" '
    r'data-next-boundary-key="(?P<key>[^"]*)"'
)
_LOADING: Final = ' data-next-state="loading" aria-busy="true"'
_HEAD_CLOSE: Final = "</head>"
_FLAG_META: Final = "HTTP_" + REQUEST_FLAG.upper().replace("-", "_")


@dataclass(frozen=True, slots=True)
class StreamedChunk:
    """One chunk of a streamed page, as its script pushed it."""

    id: str
    state: str
    kind: str | None
    parent: str | None
    envelope: Envelope
    debug: dict[str, Any] | None = None
    digest: str | None = None

    @property
    def html(self) -> str:
        """Return the HTML of the op that settles the boundary."""
        ops = self.envelope.ops
        return (ops[0].html or "") if ops else ""

    @property
    def op(self) -> str:
        """Return the verb that settles the boundary, `inner` or `replace`."""
        ops = self.envelope.ops
        return ops[0].op if ops else ""


def _chunk(data: dict[str, Any]) -> StreamedChunk:
    return StreamedChunk(
        id=data[keys.CHUNK_BOUNDARY],
        state=data[keys.CHUNK_STATE],
        kind=data.get(keys.CHUNK_KIND),
        parent=data.get(keys.CHUNK_PARENT),
        envelope=Envelope.from_dict(data[keys.CHUNK_ENVELOPE]),
        debug=data.get(keys.CHUNK_DEBUG),
        digest=data.get(keys.CHUNK_DIGEST),
    )


def _span(document: str, ident: str) -> tuple[int, int, int, str] | None:
    """Return where the element of `ident` opens, its content starts and it ends."""
    opening = re.search(_OPENING.format(ident=re.escape(ident)), document)
    if opening is None:
        return None
    tag = opening["tag"]
    tags = re.compile(rf"<(/?){re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    depth = 1
    for found in tags.finditer(document, opening.end()):
        depth += -1 if found.group(1) else 1
        if depth == 0:
            return opening.start(), opening.end(), found.end(), found.group(0)
    return None


def _state(chunk: StreamedChunk) -> str:
    """Return the state attributes the browser sets on a wrapper it fills in place."""
    if chunk.kind is None:
        return f' data-next-state="{chunk.state}"'
    state = f' data-next-state="{chunk.state}" data-next-error="{chunk.kind}"'
    if chunk.digest is not None:
        state += f' data-next-error-digest="{escape(chunk.digest)}"'
    return state


def _applied(document: str, chunk: StreamedChunk) -> str:
    """Return `document` with one chunk applied, an unknown target dropping it."""
    span = _span(document, chunk.id)
    if span is None:
        return document
    start, content, end, closing = span
    if chunk.op == "replace":
        document = document[:start] + chunk.html + document[end:]
    else:
        opening = document[start:content].replace(_LOADING, _state(chunk), 1)
        before = document[:start] + opening + chunk.html
        document = before + closing + document[end:]
    for op in chunk.envelope.ops:
        robots = op.extras.get("robots") if op.op == "meta" else None
        if robots is not None and _HEAD_CLOSE in document:
            meta = f'<meta name="robots" content="{robots}">'
            document = document.replace(_HEAD_CLOSE, meta + _HEAD_CLOSE, 1)
    return document


def _pushed(text: str) -> object:
    """Return the payload a script pushes to `$nx`, `None` for one that pushes none."""
    found = _PUSH.search(text)
    if found is None:
        return None
    payload, _end = _JSON.raw_decode(text, found.end())
    return payload


def decoded(response: HttpResponseBase, writes: Sequence[bytes]) -> list[bytes]:
    """Return `writes` decoded from the `Content-Encoding` of `response`, in order."""
    decode = response_decoder(response)
    return [decode(write) for write in writes]


class StreamedPage:
    """A page or partial response read as a stream, its shell, chunks and tail.

    A partial stream's first `patch` frame is its `envelope`, its ops' HTML the `shell`.
    """

    cut: bool
    """Whether the body stopped before its end, a frame cut mid-way included."""

    def __init__(self, writes: list[bytes], response: HttpResponseBase) -> None:
        """Decode the writes and split them into the shell, the chunks and the rest."""
        writes = decoded(response, writes)
        self.response = response
        self.writes = writes
        self.padded = len(writes[0]) if writes else 0
        self.chunks: list[StreamedChunk] = []
        self.ended = False
        self.tail = ""
        self.hello: dict[str, Any] | None = None
        self.envelope: Envelope | None = None
        self.end: str | None = None
        self.cut = False
        if media_type(response.get("Content-Type", "")) == EVENT_STREAM:
            self._read_events(b"".join(writes).decode())
            return
        first = writes[0].decode() if writes else ""
        self.shell = _PAD.sub("", first)
        rest = b"".join(writes[1:]).decode()
        for found in SCRIPT_TEXT.finditer(rest):
            data = _pushed(found["text"])
            if data == keys.CHUNK_END:
                self.ended = True
                self.tail = _PAD.sub("", rest[found.end() :])
                break
            if isinstance(data, dict):
                self.chunks.append(_chunk(data))
        self.cut = not self.ended
        if response.streaming and writes and not (self.chunks or self.ended):
            msg = (
                "the streamed body holds no chunk and no end marker, as no script "
                "in it pushes to $nx, so check NEXT_JS_OPTIONS['chunk_template']"
            )
            raise AssertionError(msg)

    def _read_events(self, text: str) -> None:
        """Read the hello, the envelope, each boundary frame and the end code."""
        events, pending = sse_events(text)
        for event in events:
            name = event.event
            payload = json.loads(event.data)
            if name == HELLO_EVENT:
                self.hello = payload
            elif name == END_EVENT:
                self.ended = True
                self.end = payload["code"]
            elif keys.CHUNK_BOUNDARY in payload:
                self.chunks.append(_chunk(payload))
            else:
                self.envelope = Envelope.from_dict(payload[keys.CHUNK_ENVELOPE])
        ops = () if self.envelope is None else self.envelope.ops
        self.shell = "".join(op.html or "" for op in ops)
        self.cut = pending or not self.ended

    def final_html(self) -> str:
        """Return the shell with every chunk applied in order, as a browser would.

        It applies the op that settles each boundary and its robots `meta` op, and
        leaves out what the runtime does beyond that, `context` ops and assets.
        """
        document = self.shell + self.tail
        for chunk in self.chunks:
            document = _applied(document, chunk)
        return document

    def boundary(self, key_or_id: str) -> StreamedChunk:
        """Return the chunk of the boundary with this wire id or stable key."""
        named = {
            found["key"]: found["id"]
            for text in (self.shell, *(chunk.html for chunk in self.chunks))
            for found in _KEYED.finditer(text)
        }
        ident = named.get(key_or_id, key_or_id)
        for chunk in self.chunks:
            if chunk.id == ident:
                return chunk
        msg = f"no chunk settles the boundary {key_or_id!r}"
        raise AssertionError(msg)


def _streamed(response: HttpResponseBase, url: str) -> StreamingHttpResponse:
    """Return `response` as a stream, or refuse it with the reason `DEBUG` names."""
    if isinstance(response, StreamingPageResponse | PartialStream):
        return response
    reason = response.get("X-Next-Stream", "")
    msg = f"the page at {url!r} answered {response.status_code} without a stream"
    raise AssertionError(f"{msg} {reason}".strip())


def _accepting(extra: dict[str, Any]) -> dict[str, Any]:
    """Return `extra` asking for a stream when it makes a partial request.

    The header joins `headers`, which both clients send, and a named `Accept` stays.
    """
    headers = extra.get("headers")
    sent = dict(headers) if isinstance(headers, Mapping) else {}
    named = {str(name).lower() for name in sent}
    partial = REQUEST_FLAG.lower() in named or _FLAG_META in extra
    if partial and "accept" not in named and "HTTP_ACCEPT" not in extra:
        return {**extra, "headers": {**sent, "Accept": STREAM_ACCEPT}}
    return extra


def read_stream(client: Client, url: str, **extra: object) -> StreamedPage:
    """GET `url` with `client` as a stream and read the whole body.

    A partial request asks for `text/event-stream` unless it names its own `Accept`,
    and an `after_shell` boundary gate opens once the shell is read.
    """
    forwarded = _accepting(extra)
    held = HeldFailures()
    with held.recording():
        with streaming():
            response = client.get(url, **forwarded)
        stream = _streamed(response, url)
        writes: list[bytes] = []
        for part in stream:
            writes.append(part)
            if len(writes) == 1:
                release_gates()
        page = StreamedPage(writes, stream)
    held.settle(b"".join(page.writes))
    return page


async def aread_stream(client: AsyncClient, url: str, **extra: object) -> StreamedPage:
    """GET `url` with an async client as a stream and read the whole body.

    An `after_shell` boundary gate opens once the shell is read.
    """
    forwarded = _accepting(extra)
    held = HeldFailures()
    with held.recording():
        with streaming():
            response = await client.get(url, **forwarded)
        stream = _streamed(response, url)
        writes: list[bytes] = []
        async for part in stream:
            writes.append(part)
            if len(writes) == 1:
                release_gates()
        page = StreamedPage(writes, stream)
    held.settle(b"".join(page.writes))
    return page


__all__ = ["StreamedChunk", "StreamedPage", "aread_stream", "read_stream"]
