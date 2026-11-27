import asyncio
import json
import time
import zlib
from collections.abc import AsyncGenerator, Callable, Generator
from typing import override
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import RequestFactory

from next.boundaries.failures import ErrorKind
from next.compression.manager import OPT_OUT
from next.defer import PartialStream, page_stream_closed, page_stream_opened
from next.defer.assembly.jobs import StreamSource
from next.defer.coordinator import Finish, Machine, Totals
from next.defer.ledger import ResponseJobs
from next.defer.markers import Delta, Job, Piece, Rendered
from next.defer.policy import StreamSlot, StreamSlots, open_streams
from next.defer.response import Payloads, StreamGrant, errored, shell, wrappers
from next.defer.response.partial import CACHE_CONTROL, CONTENT_TYPE, frame
from next.diagnostics import reset_failure_logs
from next.partial.backends import JsonPartialProtocolBackend
from next.partial.envelope import Envelope, Patch
from next.partial.sse import sse_heartbeat_seconds
from next.testing import SseEvent, capture_signals, override_next_settings, sse_events
from tests.support.boundaries import make_call, make_job, open_session
from tests.support.waits import until_loop


_MACHINERY = "machinery"

pytestmark = pytest.mark.django_db


class _Batches:
    """The batches a test lists, failing after the last one when asked to.

    `failed` stands for a failed coordinator owing `owed`, `None` failing to render it.
    """

    def __init__(
        self, *batches: tuple[Piece, ...], fail: bool = False, failed: bool = False
    ) -> None:
        self.batches = batches
        self.fail = fail
        self.failed = failed
        self.owed: tuple[Piece, ...] | None = ()
        self.closed = False
        self.idle: float | None = None
        self.finish: Finish | None = Finish(
            aborted=False, cut=False, totals=Totals(late=1, shed=2)
        )
        self.pinned: str | None = None

    def pinning_rule(self) -> str | None:
        return self.pinned

    def unsettled(self) -> tuple[Piece, ...]:
        if self.owed is None:
            raise RuntimeError(_MACHINERY)
        return self.owed

    def run(
        self, *, idle: float | None = None
    ) -> Generator[tuple[Piece, ...], None, None]:
        self.idle = idle
        try:
            yield from self.batches
            if self.fail:
                raise RuntimeError(_MACHINERY)
        finally:
            self.closed = True

    async def arun(self) -> AsyncGenerator[tuple[Piece, ...], None]:
        try:
            for batch in self.batches:
                await asyncio.sleep(0)
                yield batch
            if self.fail:
                raise RuntimeError(_MACHINERY)
        finally:
            self.closed = True


type _Held = dict[str, tuple[str | None, str]]


def _failed(error: ErrorKind) -> Rendered:
    return Rendered("<p>failed</p>", Delta(), kind=error)


def _queued(session: ResponseJobs, key: str) -> Job:
    """Queue a job of `key` that renders ready content, failing to its error UI."""
    job = make_job(session, "<p>ok</p>", key=key, error_ui=_failed)
    session.queue(job)
    return job


def _piece(kind: ErrorKind | None = None) -> tuple[Piece, _Held]:
    """Return one settled piece and the loading wrapper the envelope carried."""
    session = open_session()
    job = make_job(session, "", error_ui=_failed)
    html = shell(job.id, make_call("div", "k"), "<p>wait</p>")
    rendered = Rendered("<p>ok</p>" if kind is None else "", Delta(), kind=kind)
    return Piece(job, rendered), wrappers(html, session.response_id)


class _Flaky(Payloads):
    """Payloads whose first `fails` mappings raise, as a frame that cannot be built."""

    def __init__(self, request: HttpRequest, held: _Held, fails: int) -> None:
        super().__init__(request, shell=held, collector=None)
        self.fails = fails

    @override
    def payload(self, piece: Piece) -> dict[str, object]:
        if self.fails:
            self.fails -= 1
            raise RuntimeError(_MACHINERY)
        return super().payload(piece)


@pytest.fixture()
def make_stream(rf: RequestFactory) -> Callable[..., tuple[PartialStream, _Batches]]:
    """Build a partial stream over the batches given, with the source that feeds it."""

    def build(
        *batches: tuple[Piece, ...],
        held: _Held | None = None,
        fail: bool = False,
        failed: bool = False,
        fails: int = 0,
        deadline: float | None = None,
        envelope: Envelope | None = None,
        request: HttpRequest | None = None,
        compress: bool = True,
        slot: StreamSlot | None = None,
    ) -> tuple[PartialStream, _Batches]:
        asked = rf.get("/") if request is None else request
        source = _Batches(*batches, fail=fail, failed=failed)
        stream = PartialStream(
            asked,
            envelope or Envelope("v", (Patch("morph", {"zone": "z"}, "<p>wait</p>"),)),
            source,
            _Flaky(asked, held or {}, fails),
            deadline=time.monotonic() + 10 if deadline is None else deadline,
            grant=StreamGrant(slot, reason="partial-stream"),
            compress=compress,
        )
        return stream, source

    return build


def _decoded(writes: list[bytes]) -> list[tuple[str, dict[str, object]]]:
    body = b"".join(writes).decode()
    return [(event.event, json.loads(event.data)) for event in sse_events(body)[0]]


def _data(written: str) -> str:
    return written.split("\n")[1].removeprefix("data: ")


class TestFrame:
    """One event holds its JSON on one `data:` line whatever the payload carries."""

    def test_line_breaks_inside_a_payload_stay_escaped(self) -> None:
        text = "a\nb\r\ndata: evil\n\nevent: end\n" + chr(0x2028) + chr(0x2029)
        written = frame("patch", {"html": text}).decode()
        assert written.count("\n") == 3
        assert written.startswith("event: patch\ndata: {")
        assert written.endswith("}\n\n")
        assert "\r" not in written
        assert sse_events(written) == ([SseEvent("patch", _data(written))], False)
        assert json.loads(_data(written)) == {"html": text}


class TestPartialStream:
    """The body writes the hello and the envelope, a patch per boundary, then the end."""

    def test_the_headers_mark_a_private_event_stream(self, make_stream) -> None:
        stream, _ = make_stream()
        stream.close()
        assert stream["Content-Type"] == CONTENT_TYPE
        assert stream["Cache-Control"] == CACHE_CONTROL
        assert CACHE_CONTROL == "private, no-cache, no-store, no-transform"
        assert stream["X-Accel-Buffering"] == "no"
        assert {"Accept", "Accept-Encoding", "X-Next-Request"} <= {
            name.strip() for name in stream["Vary"].split(",")
        }
        assert not stream.has_header("Content-Length")

    def test_the_frames_follow_the_batches(self, make_stream) -> None:
        piece, held = _piece()
        stream, source = make_stream((piece,), held=held)
        with capture_signals(page_stream_closed) as recorder:
            writes = list(stream)
            stream.close()
        assert len(writes[0]) == 1024
        assert writes[0].startswith(
            b'event: hello\ndata: {"protocol":2,"streamed":true}'
        )
        assert b"retry:" not in b"".join(writes)
        assert b"\nid:" not in b"".join(writes)
        events = _decoded(writes)
        assert [name for name, _ in events] == ["hello", "patch", "patch", "end"]
        assert set(events[1][1]) == {"e"}
        assert events[2][1]["z"] == piece.id
        assert events[2][1]["s"] == "ready"
        assert events[-1][1] == {"code": "complete"}
        assert source.closed is True
        (event,) = recorder.events
        assert event.kwargs["partial"] is True
        assert event.kwargs["aborted"] is False
        assert (event.kwargs["sent"], event.kwargs["late"]) == (1, 1)

    def test_an_idle_batch_writes_a_heartbeat(self, make_stream) -> None:
        piece, held = _piece()
        stream, source = make_stream((), (piece,), held=held)
        writes = list(stream)
        stream.close()
        assert writes[1] == b": ping\n\n"
        assert source.idle == sse_heartbeat_seconds()
        assert [name for name, _ in _decoded(writes)] == [
            "hello",
            "patch",
            "patch",
            "end",
        ]

    def test_a_long_envelope_is_not_padded(self, make_stream) -> None:
        envelope = Envelope("v", (Patch("morph", {"zone": "z"}, "x" * 2000),))
        stream, _ = make_stream(envelope=envelope)
        first = next(iter(stream))
        stream.close()
        assert not first.endswith(b"  \n\n")
        assert first.endswith(b"}\n\n")

    def test_a_machinery_failure_ends_with_error(
        self, make_stream, caplog: pytest.LogCaptureFixture
    ) -> None:
        stream, _ = make_stream(fail=True)
        with caplog.at_level("ERROR", logger="next.defer"):
            writes = list(stream)
        assert _decoded(writes)[-1] == ("end", {"code": "error"})
        assert caplog.text.count("The stream of a page failed") == 1

    def test_a_coordinator_failure_settles_the_rest_before_the_end(
        self, make_stream
    ) -> None:
        ready, held = _piece()
        lost, lost_held = _piece()
        stream, source = make_stream((ready,), held={**held, **lost_held}, failed=True)
        source.owed = (errored(lost),)
        with capture_signals(page_stream_closed) as recorder:
            writes = list(stream)
            stream.close()
        events = _decoded(writes)
        assert [name for name, _ in events] == [
            "hello",
            "patch",
            "patch",
            "patch",
            "end",
        ]
        assert (events[2][1]["z"], events[2][1]["s"]) == (ready.id, "ready")
        settled = events[3][1]
        assert (settled["z"], settled["s"], settled["k"]) == (lost.id, "error", "error")
        assert "<p>failed</p>" in json.dumps(settled["e"])
        assert events[-1] == ("end", {"code": "error"})
        assert writes[-1].startswith(b"event: patch")
        (event,) = recorder.events
        assert (event.kwargs["sent"], event.kwargs["failed"]) == (2, 1)
        assert event.kwargs["aborted"] is False

    def test_owed_frames_that_fail_still_end_the_stream(
        self, make_stream, caplog: pytest.LogCaptureFixture
    ) -> None:
        stream, source = make_stream(failed=True)
        source.owed = None
        with caplog.at_level("ERROR", logger="next.defer"):
            events = _decoded(list(stream))
        assert [name for name, _ in events] == ["hello", "patch", "end"]
        assert events[-1] == ("end", {"code": "error"})
        assert caplog.text.count("The stream of a page failed") == 1

    def test_a_frame_that_fails_settles_its_boundary_as_an_error(
        self, make_stream, caplog: pytest.LogCaptureFixture
    ) -> None:
        piece, held = _piece()
        stream, _ = make_stream((piece,), held=held, fails=1)
        with caplog.at_level("ERROR", logger="next.defer"):
            events = _decoded(list(stream))
        settled = events[2][1]
        assert (settled["z"], settled["s"], settled["k"]) == (
            piece.id,
            "error",
            "error",
        )
        assert "<p>failed</p>" in json.dumps(settled["e"])
        assert events[-1] == ("end", {"code": "error"})
        assert caplog.text.count("A frame of a stream failed to build") == 1

    def test_a_second_failure_is_contained_without_a_second_log(
        self, make_stream, caplog: pytest.LogCaptureFixture
    ) -> None:
        piece, held = _piece()
        stream, source = make_stream((piece,), held=held, fails=1)
        source.owed = None
        with caplog.at_level("ERROR", logger="next.defer"):
            events = _decoded(list(stream))
        assert [name for name, _ in events] == ["hello", "patch", "patch", "end"]
        assert events[-1] == ("end", {"code": "error"})
        assert len(caplog.records) == 1

    def test_an_error_frame_that_fails_too_ends_the_stream(self, make_stream) -> None:
        piece, held = _piece()
        stream, source = make_stream((piece,), (piece,), held=held, fails=2)
        events = _decoded(list(stream))
        assert [name for name, _ in events] == ["hello", "patch", "end"]
        assert events[-1] == ("end", {"code": "error"})
        assert source.closed is True

    def test_an_opening_no_byte_spells_raises_before_any_header(
        self, make_stream
    ) -> None:
        envelope = Envelope("v", (Patch("morph", {"zone": "z"}, "x\ud800y"),))
        before = open_streams.open
        with pytest.raises(UnicodeEncodeError):
            make_stream(envelope=envelope)
        assert open_streams.open == before

    def test_the_stream_serialises_as_the_json_envelope_does(self, make_stream) -> None:
        envelope = Envelope("v", (Patch("morph", {"zone": "z"}, "jos\u00e9 \u2028"),))
        stream, _ = make_stream(envelope=envelope)
        first = next(iter(stream))
        stream.close()
        body = JsonPartialProtocolBackend().serialize_envelope(envelope)
        assert b'"e":' + body + b"}" in first

    def test_a_coded_stream_still_marks_itself_no_transform(
        self, make_stream, rf
    ) -> None:
        piece, held = _piece()
        request = rf.get("/", HTTP_ACCEPT_ENCODING="gzip")
        stream, _ = make_stream((piece,), held=held, request=request)
        assert stream["Content-Encoding"] == "gzip"
        assert "no-transform" in stream["Cache-Control"]
        writes = list(stream)
        stream.close()
        assert _decoded([zlib.decompress(b"".join(writes), 31)])[-1] == (
            "end",
            {"code": "complete"},
        )

    def test_a_real_coordinator_whose_machine_fails_settles_every_boundary(
        self, rf, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = open_session()
        jobs = [_queued(session, key) for key in ("first", "second")]
        held = wrappers(
            "".join(
                shell(job.id, make_call("div", job.key), "<p>wait</p>") for job in jobs
            ),
            session.response_id,
        )
        request = rf.get("/")
        broken = mock.patch.object(Machine, "finished", side_effect=RuntimeError("x"))
        with broken, caplog.at_level("ERROR", logger="next.defer"):
            source = StreamSource(session, request)
            stream = PartialStream(
                request,
                Envelope("v", (Patch("morph", {"zone": "z"}, "<p>wait</p>"),)),
                source,
                Payloads(request, shell=held, collector=None),
                deadline=session.deadline,
            )
            events = _decoded(list(stream))
            stream.close()
        settled = [(data["z"], data["s"], data["k"]) for _, data in events[2:-1]]
        assert settled == [(job.id, "error", "error") for job in jobs]
        assert events[-1] == ("end", {"code": "error"})
        assert source.failed is True
        assert caplog.text.count("The driver of a response's deferred jobs failed") == 1

    def test_a_timeout_past_the_deadline_ends_with_deadline(self, make_stream) -> None:
        piece, held = _piece(ErrorKind.TIMEOUT)
        stream, _ = make_stream((piece,), held=held, deadline=time.monotonic())
        events = _decoded(list(stream))
        assert (events[2][1]["s"], events[2][1]["k"]) == ("error", "timeout")
        assert events[-1] == ("end", {"code": "deadline"})

    def test_a_timeout_before_the_deadline_still_completes(self, make_stream) -> None:
        piece, held = _piece(ErrorKind.TIMEOUT)
        stream, _ = make_stream((piece,), held=held)
        assert _decoded(list(stream))[-1] == ("end", {"code": "complete"})

    def test_an_unread_stream_closed_frees_its_slot_unannounced(
        self, make_stream
    ) -> None:
        before = open_streams.open
        stream, _ = make_stream(slot=open_streams.acquire(100))
        assert open_streams.open == before + 1
        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            stream.close()
            stream.close()
        assert open_streams.open == before
        assert recorder.events == []

    def test_the_opening_is_announced_as_the_body_starts(self, make_stream) -> None:
        piece, held = _piece()
        stream, source = make_stream((piece,), held=held)
        source.pinned = "atomic"
        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            assert recorder.events == []
            list(stream)
            stream.close()
        opened, closed = recorder.events
        assert opened.signal is page_stream_opened
        assert opened.kwargs["request_lane"] == "atomic"
        assert opened.kwargs["reason"] == "partial-stream"
        assert opened.kwargs["partial"] is True
        assert closed.kwargs["aborted"] is False

    def test_closing_mid_stream_frees_the_slot_once(self, make_stream) -> None:
        piece, held = _piece()
        before = open_streams.open
        stream, _ = make_stream((piece,), held=held, slot=open_streams.acquire(100))
        with capture_signals(page_stream_closed) as recorder:
            iterator = iter(stream)
            next(iterator)
            during = open_streams.open
            stream.close()
        assert during == before + 1
        assert open_streams.open == before
        assert [event.kwargs["aborted"] for event in recorder.events] == [True]

    def test_a_page_opting_out_keeps_the_stream_plain(self, make_stream, rf) -> None:
        request = rf.get("/", HTTP_ACCEPT_ENCODING="gzip")
        stream, _ = make_stream(request=request, compress=False)
        stream.close()
        assert not stream.has_header("Content-Encoding")
        assert getattr(stream, OPT_OUT) is False


class TestAsyncPartialStream:
    """Under ASGI the body runs on the server loop with the same frames."""

    def test_the_frames_follow_the_batches(self, async_rf, make_stream, rf) -> None:
        piece, held = _piece()

        async def consume() -> tuple[PartialStream, list[bytes]]:
            stream, _ = make_stream((piece,), held=held, request=async_rf.get("/"))
            writes = [part async for part in stream]
            await sync_to_async(stream.close)()
            return stream, writes

        stream, writes = asyncio.run(consume())
        assert stream.is_async is True
        assert [name for name, _ in _decoded(writes)] == [
            "hello",
            "patch",
            "patch",
            "end",
        ]

    def test_a_suspended_body_closes_on_the_loop(
        self, async_rf, make_stream, rf
    ) -> None:
        piece, held = _piece()
        before = open_streams.open

        async def abandon() -> tuple[int, bool]:
            stream, source = make_stream(
                (piece,),
                held=held,
                request=async_rf.get("/"),
                slot=open_streams.acquire(100),
            )
            body = stream.__aiter__()
            await anext(body)
            await anext(body)
            during = open_streams.open
            await sync_to_async(stream.close)()
            # The close queues the body for the next loop turn, which a hop that
            # ended before its future was chained returns without taking.
            await asyncio.sleep(0)
            async with asyncio.timeout(5):
                others = asyncio.all_tasks() - {asyncio.current_task()}
                await asyncio.gather(*others, return_exceptions=True)
            return during, source.closed

        with capture_signals(page_stream_closed) as recorder:
            during, closed = asyncio.run(abandon())
        assert closed is True
        assert during == before + 1
        assert open_streams.open == before
        assert [event.kwargs["aborted"] for event in recorder.events] == [True]

    def test_a_failure_settles_on_the_request_thread(
        self, async_rf, make_stream, rf
    ) -> None:
        piece, held = _piece()
        lost, lost_held = _piece()

        async def consume() -> list[bytes]:
            stream, source = make_stream(
                (piece,),
                held={**held, **lost_held},
                failed=True,
                fails=1,
                request=async_rf.get("/"),
            )
            source.owed = (errored(lost),)
            return [part async for part in stream]

        events = _decoded(asyncio.run(consume()))
        assert [(data.get("z"), data.get("s")) for _, data in events[2:4]] == [
            (piece.id, "error"),
            (lost.id, "error"),
        ]
        assert events[-1] == ("end", {"code": "error"})

    def test_a_negotiated_coding_compresses_each_write(
        self, async_rf, make_stream, rf
    ) -> None:
        piece, held = _piece()
        request = async_rf.get("/", headers={"Accept-Encoding": "gzip"})

        async def consume() -> tuple[PartialStream, list[bytes]]:
            stream, _ = make_stream((piece,), held=held, request=request)
            return stream, [part async for part in stream]

        stream, writes = asyncio.run(consume())
        assert stream["Content-Encoding"] == "gzip"
        assert writes[0][:2] == b"\x1f\x8b"


class TestPartialWriteTimeout:
    """A partial stream under ASGI also ends once its client takes no write."""

    def test_a_stalled_client_frees_the_slot_and_the_jobs(
        self, async_rf, make_stream, rf, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        piece, held = _piece()
        slots = StreamSlots()

        async def read() -> tuple[int, bool, list[bytes]]:
            stream, source = make_stream(
                (piece,), held=held, request=async_rf.get("/"), slot=slots.acquire(1)
            )
            body = stream.__aiter__()
            await anext(body)
            await anext(body)
            await until_loop(lambda: slots.open == 0)
            during = (slots.open, source.closed)
            rest = [part async for part in body]
            await sync_to_async(stream.close)()
            return *during, rest

        with (
            override_next_settings(DEFER={"WRITE_TIMEOUT": 0.05}),
            caplog.at_level("WARNING", logger="next.defer"),
        ):
            open_after, closed, rest = asyncio.run(read())
        assert (open_after, closed, rest) == (0, True, [])
        assert "WRITE_TIMEOUT" in caplog.text
