import asyncio
import gc
import inspect
import json
import zlib
from collections.abc import AsyncGenerator, Callable, Generator, Iterator
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from next.compression import OPT_OUT, StreamEncoding
from next.defer import Addressing, BoundaryFailure, DeferMode, ErrorKind, Site
from next.diagnostics import degraded_status, reset_failure_logs, watch_degraded
from next.middleware import CompressionMiddleware
from next.partial import keys
from next.static.assets import StaticAsset
from next.static.collector import StaticCollector
from next.static.runtime import BUNDLE_NOTE, URGENT_NOTE, NextScriptBuilder
from next.suspense import page_stream_closed, page_stream_opened
from next.suspense.coordinator import Totals
from next.suspense.markers import (
    Job,
    Lane,
    Outcome,
    Placement,
    Rendered,
    Timing,
    Verdict,
)
from next.suspense.policy import StreamSlot, StreamSlots, open_streams
from next.suspense.render import Added, Delta, Merged, Noted
from next.suspense.response import (
    Chunks,
    Document,
    Piece,
    StreamGrant,
    StreamingPageResponse,
    apart,
    dress,
    release_connections,
    shell,
    wrappers,
)
from next.suspense.response.stream import errored
from next.suspense.session import BoundarySession
from next.testing import capture_signals, override_next_settings
from tests.support.boundaries import COMPONENT_SITE, make_call, make_job, open_session


_PAGE = Site(Addressing.PAGE, "shop/loading.djx", DeferMode.STREAM, "layout.djx")
_CSS = StaticAsset(url="/static/card.css", kind="css")
_DEFER = StaticAsset(url="/static/next/next.defer.min.js", kind="js")
_RELOAD = StaticAsset(url="/static/next/next.reload.min.js", kind="js")
_BUILDER = NextScriptBuilder("/static/next/next.min.js")
_LINE = chr(0x2028)
_PARAGRAPH = chr(0x2029)
_MACHINERY = "machinery"
_CONSUMER = "consumer"


pytestmark = pytest.mark.django_db


def _job(
    session: BoundarySession[Delta],
    site: Site = COMPONENT_SITE,
    parent: str | None = None,
) -> Job[Delta]:
    return make_job(
        session,
        "",
        site=site,
        key=f"key-{len(session.jobs)}",
        parent=parent,
        fallback=lambda kind: Rendered("", Delta(), kind=kind),
    )


@pytest.fixture()
def make_chunks(rf: RequestFactory) -> Callable[..., Chunks]:
    """Build the chunks of a shell, over a plain GET request."""

    def build(
        session: BoundarySession[Delta],
        html: str,
        collector: StaticCollector | None = None,
    ) -> Chunks:
        return Chunks(
            rf.get("/"),
            shell=wrappers(html, session.seal),
            collector=collector,
            builder=_BUILDER,
            nonce="n0",
        )

    return build


def _payload(script: str) -> object:
    start = script.index(".push(") + len(".push(")
    return json.loads(script[start : script.rindex(")</script>")])


def _failed(job: Job[Delta], html: str) -> Piece:
    """Return the piece of `job` settled as an error whose digest is `d1g`."""
    failure = BoundaryFailure.of(
        ErrorKind.ERROR, "d1g", retry=False, message="", traceback=""
    )
    rendered = Rendered(html, Delta(), kind=ErrorKind.ERROR, failure=failure)
    return Piece(job, Outcome(job.id, rendered))


class TestDress:
    """The shell of a stream marks its root and hides loading UI without scripts."""

    def test_the_root_and_the_head_are_dressed_before_the_split(self) -> None:
        html = "<html><head></head><body><p>x</p></body></html>"
        split = html.index("</body>")
        document = dress(html, split, nonce="abc", noscript=True)
        assert document.head.startswith("<html data-next-streamed><head><noscript>")
        assert '<style nonce="abc">' in document.head
        assert document.tail == "</body></html>"

    def test_a_body_without_html_or_head_takes_the_style_at_its_end(self) -> None:
        document = dress("<body><p>x</p></body>", 14, nonce=None, noscript=True)
        assert document.head.startswith("<body data-next-streamed>")
        assert document.head.endswith("display:none}</style></noscript>")

    def test_a_fragment_without_noscript_is_left_alone(self) -> None:
        document = dress("<p>x</p>", 8, nonce=None, noscript=False)
        assert document == Document("<p>x</p>", "")


class TestChunks:
    """Each settled job becomes one script pushing a wrapper around its envelope."""

    def test_a_ready_boundary_is_an_inner_op(self, make_chunks) -> None:
        session = open_session()
        job = _job(session)
        html = shell(job.id, make_call("div", job.key), "<p>wait</p>")
        rendered = Rendered("<p>ready</p>", Delta())
        script = make_chunks(session, html).script(
            Piece(job, Outcome(job.id, rendered))
        )
        assert script.startswith('<script nonce="n0">(self.$nx||=[]).push({')
        data = _payload(script)
        assert data == {
            "z": job.id,
            "s": "ready",
            "e": {
                "version": mock.ANY,
                "ops": [
                    {
                        "op": "inner",
                        "target": {"boundary": job.id},
                        "html": "<p>ready</p>",
                    }
                ],
                "assets": [],
                "form": None,
            },
        }

    def test_an_adopted_root_is_replaced_whole(self, make_chunks) -> None:
        session = open_session()
        job = _job(session)
        html = shell(job.id, make_call(None, job.key), "<tr><td>wait</td></tr>")
        rendered = Rendered("<tr><td>row</td></tr>", Delta())
        data = make_chunks(session, html).payload(Piece(job, Outcome(job.id, rendered)))
        (op,) = data[keys.CHUNK_ENVELOPE]["ops"]
        assert op["op"] == "replace"
        assert op["html"].startswith(f'<tr id="{job.id}"')
        assert 'data-next-state="ready"' in op["html"]

    def test_an_error_names_its_kind_and_a_nested_one_its_parent(
        self, make_chunks
    ) -> None:
        session = open_session()
        parent = _job(session)
        child = _job(session, parent=parent.id)
        html = shell(child.id, make_call("div", child.key), "")
        rendered = Rendered("<p>failed</p>", Delta(), kind=ErrorKind.TIMEOUT)
        piece = Piece(child, Outcome(child.id, rendered), parent=parent.key)
        data = make_chunks(session, html).payload(piece)
        assert data[keys.CHUNK_STATE] == "error"
        assert data[keys.CHUNK_KIND] == "timeout"
        assert data[keys.CHUNK_PARENT] == parent.key

    @override_settings(DEBUG=True)
    def test_a_debug_failure_sends_its_digest_beside_an_inner_op(
        self, make_chunks
    ) -> None:
        session = open_session()
        job = _job(session)
        html = shell(job.id, make_call("div", job.key), "")
        data = make_chunks(session, html).payload(_failed(job, "<p>failed</p>"))
        assert (data[keys.CHUNK_STATE], data[keys.CHUNK_DIGEST]) == ("error", "d1g")
        (op,) = data[keys.CHUNK_ENVELOPE]["ops"]
        assert (op["op"], op["html"]) == ("inner", "<p>failed</p>")

    @override_settings(DEBUG=True)
    def test_an_adopted_failure_also_carries_its_digest_on_the_root(
        self, make_chunks
    ) -> None:
        session = open_session()
        job = _job(session)
        html = shell(job.id, make_call(None, job.key), "<tr><td>wait</td></tr>")
        data = make_chunks(session, html).payload(_failed(job, "<tr><td>x</td></tr>"))
        assert data[keys.CHUNK_DIGEST] == "d1g"
        (op,) = data[keys.CHUNK_ENVELOPE]["ops"]
        assert op["op"] == "replace"
        assert 'data-next-error-digest="d1g"' in op["html"]

    def test_a_failure_outside_debug_sends_no_digest(self, make_chunks) -> None:
        session = open_session()
        job = _job(session)
        html = shell(job.id, make_call("div", job.key), "")
        data = make_chunks(session, html).payload(_failed(job, "<p>failed</p>"))
        assert data[keys.CHUNK_STATE] == "error"
        assert keys.CHUNK_DIGEST not in data

    def test_a_ready_boundary_sends_no_digest(self, make_chunks) -> None:
        session = open_session()
        job = _job(session)
        html = shell(job.id, make_call("div", job.key), "")
        rendered = Rendered("<p>ready</p>", Delta())
        data = make_chunks(session, html).payload(Piece(job, Outcome(job.id, rendered)))
        assert keys.CHUNK_DIGEST not in data

    def test_a_missing_page_adds_a_noindex_meta_op(self, make_chunks) -> None:
        session = open_session()
        job = _job(session, _PAGE)
        html = shell(job.id, make_call("div", job.key), "")
        rendered = Rendered("", Delta(), kind=ErrorKind.NOT_FOUND)
        data = make_chunks(session, html).payload(Piece(job, Outcome(job.id, rendered)))
        ops = data[keys.CHUNK_ENVELOPE]["ops"]
        assert ops[-1] == {"op": "meta", "robots": "noindex"}

    def test_a_nested_wrapper_loses_its_markers_and_is_known_later(
        self, make_chunks
    ) -> None:
        session = open_session()
        parent = _job(session)
        child = _job(session, parent=parent.id)
        nested = shell(child.id, make_call(None, child.key), "<li>wait</li>")
        chunks = make_chunks(
            session, shell(parent.id, make_call("div", parent.key), "")
        )
        first = chunks.payload(
            Piece(parent, Outcome(parent.id, Rendered(nested, Delta())))
        )
        inner = first[keys.CHUNK_ENVELOPE]["ops"][0]["html"]
        assert "<!--" not in inner
        second = chunks.payload(
            Piece(child, Outcome(child.id, Rendered("<li>x</li>", Delta())))
        )
        assert second[keys.CHUNK_ENVELOPE]["ops"][0]["op"] == "replace"

    def test_the_ledger_sends_each_asset_once_and_none_the_shell_had(
        self, make_chunks
    ) -> None:
        session = open_session()
        first, second = _job(session), _job(session)
        html = shell(first.id, make_call("div", "a"), "") + shell(
            second.id, make_call("div", "b"), ""
        )
        shared = StaticAsset(url="/static/shared.css", kind="css")
        held = StaticCollector()
        held.add(_CSS)
        chunks = make_chunks(session, html, held)
        delta = Delta((Added(_CSS), Added(shared), Merged("count", 1)))
        one = chunks.payload(Piece(first, Outcome(first.id, Rendered("", delta))))
        two = chunks.payload(Piece(second, Outcome(second.id, Rendered("", delta))))
        assert [a["url"] for a in one[keys.CHUNK_ENVELOPE]["assets"]] == [
            "/static/shared.css"
        ]
        assert two[keys.CHUNK_ENVELOPE]["assets"] == []
        assert {"op": "context", "data": {"count": 1}} in (
            two[keys.CHUNK_ENVELOPE]["ops"]
        )

    @override_settings(DEBUG=True)
    def test_debug_carries_the_lane_and_the_timing(self, make_chunks) -> None:
        session = open_session()
        job = _job(session)
        timing = Timing(Lane.POOL, Placement(Verdict.FOREGROUND_FULL), 0.002, 0.01)
        outcome = Outcome(job.id, Rendered("", Delta()), timing)
        chunks = make_chunks(session, shell(job.id, make_call("div", "k"), ""))
        assert chunks.payload(Piece(job, outcome))[keys.CHUNK_DEBUG] == {
            "lane": "pool",
            "placement": "foreground-full",
            "queue_ms": 2.0,
            "run_ms": 10.0,
        }
        bare = Outcome(job.id, Rendered("", Delta()))
        assert chunks.payload(Piece(job, bare))[keys.CHUNK_DEBUG] == {
            "lane": None,
            "placement": None,
            "queue_ms": 0.0,
            "run_ms": 0.0,
        }

    def test_the_status_of_an_error_ui_never_reaches_a_stream(
        self, make_chunks
    ) -> None:
        session = open_session()
        job = _job(session)
        rendered = Rendered("", Delta(), status=500)
        chunks = make_chunks(session, shell(job.id, make_call("div", "k"), ""))
        watch_degraded()
        chunks.script(Piece(job, Outcome(job.id, rendered)))
        assert degraded_status() is None

    def test_a_note_for_the_injection_is_reported_once(
        self, make_chunks, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = open_session()
        job = _job(session)
        delta = Delta((Noted("next.scripts.script", "chart"),))
        chunks = make_chunks(session, shell(job.id, make_call("div", "k"), ""))
        reset_failure_logs()
        with caplog.at_level("WARNING", logger="next.suspense"):
            for _ in range(2):
                chunks.payload(Piece(job, Outcome(job.id, Rendered("", delta))))
        [record] = caplog.records
        assert "'next.scripts.script'" in record.getMessage()
        assert "defer='inline'" in record.getMessage()

    def test_a_needed_bundle_rides_the_chunk_once_and_never_warns(
        self, make_chunks, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = open_session()
        first, second, third = _job(session), _job(session), _job(session)
        html = "".join(
            shell(job.id, make_call("div", job.key), "")
            for job in (first, second, third)
        )
        held = StaticCollector()
        held.add(_RELOAD)
        chunks = make_chunks(session, html, held)
        delta = Delta(
            (
                Added(_DEFER),
                Noted(BUNDLE_NOTE, _DEFER.url),
                Noted(URGENT_NOTE, True),
                Added(_RELOAD),
                Noted(BUNDLE_NOTE, _RELOAD.url),
            )
        )
        reset_failure_logs()
        with caplog.at_level("WARNING", logger="next.suspense"):
            sent = [
                chunks.payload(Piece(job, Outcome(job.id, Rendered("", delta))))
                for job in (first, second, third)
            ]
        assert sent[0][keys.CHUNK_ENVELOPE]["assets"] == [
            {"kind": "js", "url": _DEFER.url, "load": "script"}
        ]
        assert sent[1][keys.CHUNK_ENVELOPE]["assets"] == []
        assert sent[2][keys.CHUNK_ENVELOPE]["assets"] == []
        assert caplog.records == []

    def test_the_end_marker_pushes_zero(self, make_chunks) -> None:
        session = open_session()
        assert make_chunks(session, "").end() == (
            '<script nonce="n0">(self.$nx||=[]).push(0)</script>'
        )

    @pytest.mark.parametrize("text", ["</script>", "<!--", _LINE, _PARAGRAPH])
    def test_no_text_breaks_out_of_the_script(self, make_chunks, text: str) -> None:
        session = open_session()
        job = _job(session)
        rendered = Rendered(f"<p>{text}</p>", Delta())
        chunks = make_chunks(session, shell(job.id, make_call("div", "k"), ""))
        script = chunks.script(Piece(job, Outcome(job.id, rendered)))
        body = script.removeprefix('<script nonce="n0">').removesuffix("</script>")
        assert "<" not in body
        assert _LINE not in body
        assert _PARAGRAPH not in body
        assert _payload(script)["e"]["ops"][0]["html"] == f"<p>{text}</p>"


class _Batches:
    """The batches a test lists, failing after the last one when asked to.

    `failed` stands for a coordinator whose machinery failed, owing the `owed` pieces.
    """

    def __init__(
        self, *batches: tuple[Piece, ...], fail: bool = False, failed: bool = False
    ) -> None:
        self.batches = batches
        self.fail = fail
        self.failed = failed
        self.owed: tuple[Piece, ...] = ()
        self.closed = False
        self.totals: Totals | None = Totals(late=1, shed=2)
        self.pinned: str | None = None

    def unsettled(self) -> tuple[Piece, ...]:
        return self.owed

    def pinning_rule(self) -> str | None:
        return self.pinned

    def run(self) -> Generator[tuple[Piece, ...], None, None]:
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


@pytest.fixture()
def make_stream(
    rf: RequestFactory,
) -> Callable[..., tuple[StreamingPageResponse, list[Piece]]]:
    """Build a streaming response over `batches`, with the piece each batch settles."""

    def build(
        batches: _Batches,
        *,
        head: str = "<html><body>",
        request: object = None,
        slot: StreamSlot | None = None,
    ) -> tuple[StreamingPageResponse, list[Piece]]:
        session = open_session()
        job = _job(session)
        html = head + shell(job.id, make_call("div", "k"), "<p>wait</p>")
        asked = rf.get("/") if request is None else request
        chunks = Chunks(
            asked,
            shell=wrappers(html, session.seal),
            collector=None,
            builder=_BUILDER,
            nonce=None,
        )
        piece = Piece(job, Outcome(job.id, Rendered("<p>ok</p>", Delta())))
        batches.batches = tuple((piece,) for _ in batches.batches)
        stream = StreamingPageResponse(
            asked,
            Document(html, "</body></html>"),
            batches,
            chunks,
            grant=StreamGrant(slot, boundaries=(("card", "stream"),)),
        )
        return stream, [piece]

    return build


@pytest.fixture()
def nested_chunks(
    make_chunks: Callable[..., Chunks],
) -> tuple[Chunks, tuple[Piece, ...]]:
    """Return the chunks of a shell and a batch of a parent, its child and a sibling."""
    session = open_session()
    parent = _job(session)
    sibling = _job(session)
    child = _job(session, parent=parent.id)
    html = shell(parent.id, make_call("div", parent.key), "") + shell(
        sibling.id, make_call("div", sibling.key), ""
    )
    nested = shell(child.id, make_call("div", child.key), "")
    pieces = (
        Piece(parent, Outcome(parent.id, Rendered(nested, Delta()))),
        Piece(child, Outcome(child.id, Rendered("<p>c</p>", Delta())), parent.key),
        Piece(sibling, Outcome(sibling.id, Rendered("<p>s</p>", Delta()))),
    )
    return make_chunks(session, html), pieces


def _flaky(script: Callable[[Chunks, Piece], str]) -> Callable[[Chunks, Piece], str]:
    """Return `script` failing on its first call only."""
    calls: list[Piece] = []

    def flaky(chunks: Chunks, piece: Piece) -> str:
        calls.append(piece)
        if len(calls) == 1:
            msg = "chunk"
            raise RuntimeError(msg)
        return script(chunks, piece)

    return flaky


@pytest.fixture()
def gzip_request(rf: RequestFactory) -> HttpRequest:
    """Return a GET asking for gzip."""
    return rf.get("/", HTTP_ACCEPT_ENCODING="gzip")


class TestStreamingPageResponse:
    """The body writes the shell, a write per batch, then the end and the tail."""

    def test_the_writes_follow_the_batches(self, make_stream) -> None:
        stream, _ = make_stream(_Batches((), ()))
        with capture_signals(page_stream_closed) as recorder:
            writes = list(stream)
            stream.close()
        assert len(writes) == 4
        assert len(writes[0]) == 1024
        assert writes[0].endswith(b" -->")
        assert writes[1].startswith(b"<script>(self.$nx||=[]).push(")
        assert writes[1].endswith(b")</script>")
        assert writes[-1] == b"<script>(self.$nx||=[]).push(0)</script></body></html>"
        (event,) = recorder.events
        assert event.kwargs["aborted"] is False
        assert (event.kwargs["sent"], event.kwargs["late"], event.kwargs["shed"]) == (
            2,
            1,
            2,
        )

    def test_a_long_shell_is_not_padded(self, make_stream) -> None:
        stream, [piece] = make_stream(_Batches(), head="<html><body>" + "x" * 2000)
        first = next(iter(stream))
        stream.close()
        assert first.endswith(f"<!--/{piece.job.id}-->".encode())

    def test_a_machinery_failure_still_ends_the_page(
        self, make_stream, caplog: pytest.LogCaptureFixture
    ) -> None:
        batches = _Batches((), fail=True)
        stream, _ = make_stream(batches)
        with caplog.at_level("ERROR", logger="next.suspense"):
            writes = list(stream)
        assert writes[-1].endswith(b"push(0)</script></body></html>")
        assert caplog.text.count("The stream of a page failed") == 1

    def test_a_failing_chunk_closes_the_jobs_and_ends_the_page(
        self, make_stream
    ) -> None:
        batches = _Batches(())
        stream, _ = make_stream(batches)
        with (
            mock.patch.object(Chunks, "script", side_effect=RuntimeError("chunk")),
            mock.patch.object(Chunks, "end", side_effect=RuntimeError("end")),
        ):
            writes = list(stream)
        assert batches.closed is True
        assert writes[-1] == b"</body></html>"

    def test_a_chunk_that_fails_settles_its_boundary_as_an_error(
        self, make_stream, caplog: pytest.LogCaptureFixture
    ) -> None:
        stream, _ = make_stream(_Batches(()))
        with (
            caplog.at_level("ERROR", logger="next.suspense"),
            mock.patch.object(Chunks, "script", _flaky(Chunks.script)),
        ):
            writes = list(stream)
        assert b'"s":"error","k":"error"' in writes[1]
        assert writes[-1].endswith(b"push(0)</script></body></html>")
        assert caplog.text.count("A frame of a stream failed to build") == 1

    def test_a_coordinator_failure_settles_the_rest_before_the_end(
        self, make_stream
    ) -> None:
        batches = _Batches(failed=True)
        stream, [piece] = make_stream(batches)
        batches.owed = (errored(piece),)
        with capture_signals(page_stream_closed) as recorder:
            writes = list(stream)
            stream.close()
        last = writes[-1]
        assert b'"s":"error","k":"error"' in last
        assert last.index(piece.outcome.id.encode()) < last.index(b"push(0)")
        (event,) = recorder.events
        assert (event.kwargs["sent"], event.kwargs["failed"]) == (1, 1)

    def test_a_healthy_run_owes_nothing(self, make_stream) -> None:
        batches = _Batches()
        stream, [piece] = make_stream(batches)
        batches.owed = (errored(piece),)
        writes = list(stream)
        stream.close()
        assert b'"s":"error"' not in b"".join(writes)

    def test_a_shell_no_byte_spells_raises_before_the_headers(
        self, make_stream
    ) -> None:
        before = open_streams.open
        with pytest.raises(UnicodeEncodeError):
            make_stream(_Batches(), head="<html><body>\ud800")
        assert open_streams.open == before

    def test_closing_mid_stream_cancels_and_frees_the_slot_once(
        self, make_stream
    ) -> None:
        batches = _Batches((), ())
        before = open_streams.open
        stream, _ = make_stream(batches, slot=open_streams.acquire(100))
        with capture_signals(page_stream_closed) as recorder:
            iterator = iter(stream)
            next(iterator)
            assert open_streams.open == before + 1
            next(iterator)
            stream.close()
            stream.close()
        assert batches.closed is True
        assert open_streams.open == before
        assert [event.kwargs["aborted"] for event in recorder.events] == [True]

    def test_an_unread_stream_closed_frees_its_slot_unannounced(
        self, make_stream
    ) -> None:
        before = open_streams.open
        stream, _ = make_stream(_Batches(()), slot=open_streams.acquire(100))
        assert open_streams.open == before + 1
        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            stream.close()
        assert open_streams.open == before
        assert recorder.events == []

    def test_the_opening_is_announced_as_the_body_starts(self, make_stream) -> None:
        batches = _Batches(())
        stream, _ = make_stream(batches)
        batches.pinned = "atomic"
        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            assert recorder.events == []
            writes = list(stream)
            stream.close()
        opened, closed = recorder.events
        assert opened.signal is page_stream_opened
        assert opened.kwargs["request_lane"] == "atomic"
        assert opened.kwargs["boundaries"] == (("card", "stream"),)
        assert opened.kwargs["partial"] is False
        assert closed.kwargs["aborted"] is False
        assert writes[-1].endswith(b"</body></html>")

    def test_a_negotiated_coding_compresses_every_write_decodably(
        self, make_stream, gzip_request
    ) -> None:
        stream, _ = make_stream(_Batches((), ()), request=gzip_request)
        stream.settle()
        assert stream["Content-Encoding"] == "gzip"
        assert "Accept-Encoding" in stream["Vary"]
        writes = list(stream)
        stream.close()
        decoder = zlib.decompressobj(31)
        decoded = [decoder.decompress(write) for write in writes]
        assert decoded[0].startswith(b"<html><body>")
        assert all(decoded)
        for chunk in decoded[1:3]:
            assert chunk.startswith(b"<script>(self.$nx||=[]).push(")
            assert chunk.rindex(b"<!--") > chunk.rindex(b"</script>")
        assert decoded[-1].startswith(
            b"<script>(self.$nx||=[]).push(0)</script></body></html><!--"
        )
        assert decoder.eof is True

    def test_an_identity_stream_keeps_every_middleware_off(
        self, make_stream, gzip_request, rf
    ) -> None:
        stream, _ = make_stream(_Batches(()), request=rf.get("/"))
        stream.settle()
        assert not stream.has_header("Content-Encoding")
        assert getattr(stream, OPT_OUT) is False
        assert CompressionMiddleware(lambda _request: stream)(gzip_request) is stream
        assert not stream.has_header("Content-Encoding")
        stream.close()

    def test_a_coded_stream_passes_the_middleware_untouched(
        self, make_stream, gzip_request
    ) -> None:
        stream, _ = make_stream(_Batches(()), request=gzip_request)
        stream.settle()
        assert CompressionMiddleware(lambda _request: stream)(gzip_request) is stream
        assert stream["Content-Encoding"] == "gzip"
        decoder = zlib.decompressobj(31)
        text = b"".join(decoder.decompress(write) for write in stream)
        assert text.startswith(b"<html><body>")
        assert decoder.eof is True

    def test_streams_left_to_the_middleware_are_encoded_there(
        self, make_stream, gzip_request
    ) -> None:
        stream, _ = make_stream(_Batches(()), request=gzip_request)
        with override_next_settings(COMPRESSION={"STREAMS": "middleware"}):
            stream.settle()
            assert not stream.has_header("Content-Encoding")
            CompressionMiddleware(lambda _request: stream)(gzip_request)
        assert stream["Content-Encoding"] == "gzip"
        decoder = zlib.decompressobj(31)
        decoded = [decoder.decompress(write) for write in stream]
        assert decoded[0].startswith(b"<html><body>")
        assert decoder.eof is True
        stream.close()

    def test_a_nested_chunk_never_shares_its_parents_write(
        self, nested_chunks, rf
    ) -> None:
        chunks, pieces = nested_chunks
        stream = StreamingPageResponse(
            rf.get("/"),
            Document("<html><body>", "</body></html>"),
            _Batches(pieces),
            chunks,
        )
        writes = list(stream)
        stream.close()
        assert len(writes) == 4
        assert writes[1].count(b"push(") == 1
        assert writes[2].count(b"push(") == 2
        assert pieces[1].outcome.id.encode() in writes[2]

    def test_an_empty_batch_makes_no_write(self, nested_chunks) -> None:
        _, pieces = nested_chunks
        assert apart(()) == []
        assert apart(pieces[::2]) == [pieces[::2]]

    def test_settle_sets_the_stream_headers(self, make_stream) -> None:
        stream, _ = make_stream(_Batches())
        stream["ETag"] = '"x"'
        stream["Last-Modified"] = "now"
        stream["Cache-Control"] = "public, max-age=60"
        stream.settle()
        stream.close()
        assert stream["Cache-Control"] == "private, no-cache"
        assert not stream.has_header("ETag")
        assert not stream.has_header("Last-Modified")
        assert stream["X-Accel-Buffering"] == "no"
        assert stream["Vary"] == "Accept-Encoding, Accept"

    def test_settle_keeps_a_stricter_directive_the_page_set(
        self, make_stream, gzip_request
    ) -> None:
        stream, _ = make_stream(_Batches(), request=gzip_request)
        stream["Cache-Control"] = "private, no-store, max-age=0, no-transform"
        stream.settle()
        stream.close()
        assert stream["Cache-Control"] == "no-store, no-transform, private, no-cache"
        assert not stream.has_header("Content-Encoding")
        assert getattr(stream, OPT_OUT) is False

    def test_settle_honours_the_opt_out_of_the_page(
        self, make_stream, gzip_request
    ) -> None:
        stream, _ = make_stream(_Batches(), request=gzip_request)
        setattr(stream, OPT_OUT, False)
        stream.settle()
        stream.close()
        assert not stream.has_header("Content-Encoding")

    def test_an_asgi_request_reads_the_body_asynchronously(
        self, make_stream, async_rf, rf
    ) -> None:
        request = async_rf.get("/")
        stream, _ = make_stream(_Batches((), ()), request=request)
        assert stream.is_async is True

        async def read() -> list[bytes]:
            with capture_signals(page_stream_closed) as recorder:
                writes = [part async for part in stream]
            assert [e.kwargs["aborted"] for e in recorder.events] == [False]
            return writes

        writes = asyncio.run(read())
        assert writes[-1].endswith(b"</body></html>")
        stream.close()

    def test_an_async_failure_still_ends_the_page(
        self, make_stream, async_rf, rf
    ) -> None:
        request = async_rf.get("/")
        stream, _ = make_stream(_Batches((), fail=True), request=request)

        async def read() -> list[bytes]:
            return [part async for part in stream]

        assert asyncio.run(read())[-1].endswith(b"</body></html>")

    def test_an_async_failure_settles_the_rest_on_the_request_thread(
        self, make_stream, async_rf, rf
    ) -> None:
        batches = _Batches((), failed=True)
        stream, [piece] = make_stream(batches, request=async_rf.get("/"))
        second = _job(open_session())
        batches.owed = (errored(Piece(second, piece.outcome)),)

        async def read() -> list[bytes]:
            return [part async for part in stream]

        with mock.patch.object(Chunks, "script", _flaky(Chunks.script)):
            writes = asyncio.run(read())
        assert b'"s":"error","k":"error"' in writes[1]
        assert second.id.encode() in writes[-1]
        assert writes[-1].endswith(b"push(0)</script></body></html>")

    def test_a_server_closing_a_suspended_async_body_closes_it_on_the_loop(
        self, make_stream, async_rf, rf
    ) -> None:
        request = async_rf.get("/")
        stream, _ = make_stream(_Batches((), ()), request=request)

        async def read() -> str:
            body = stream.__aiter__()
            await anext(body)
            await sync_to_async(stream.close, thread_sensitive=False)()
            for _ in range(10):
                await asyncio.sleep(0)
            return inspect.getasyncgenstate(stream._content)

        assert asyncio.run(read()) == inspect.AGEN_CLOSED

    def test_the_async_shell_compresses_off_the_loop(
        self, make_stream, async_rf, rf
    ) -> None:
        request = async_rf.get("/", headers={"Accept-Encoding": "gzip"})
        stream, _ = make_stream(_Batches((), ()), request=request)
        stream.settle()
        away: list[int] = []
        to_thread = asyncio.to_thread

        async def spy(func, *args):
            away.append(len(args[0]))
            return await to_thread(func, *args)

        async def read() -> list[bytes]:
            return [part async for part in stream]

        with mock.patch("next.compression.streams.asyncio.to_thread", spy):
            writes = asyncio.run(read())
        stream.close()
        assert away == [1024]
        decoder = zlib.decompressobj(31)
        assert b"".join(decoder.decompress(write) for write in writes).startswith(
            b"<html><body>"
        )
        assert decoder.eof is True

    def test_a_large_async_write_compresses_off_the_loop(
        self, make_stream, async_rf, rf
    ) -> None:
        request = async_rf.get("/", headers={"Accept-Encoding": "gzip"})
        stream, _ = make_stream(_Batches(()), request=request)
        away: list[int] = []
        to_thread = asyncio.to_thread

        async def spy(func, *args):
            away.append(len(args[0]))
            return await to_thread(func, *args)

        async def read() -> list[bytes]:
            return [part async for part in stream]

        with (
            override_next_settings(COMPRESSION={"OFFLOAD_BYTES": 100}),
            mock.patch("next.compression.streams.asyncio.to_thread", spy),
        ):
            stream.settle()
            asyncio.run(read())
        stream.close()
        assert away[0] == 1024
        assert len(away) == 2

    def test_closing_an_unread_async_stream_frees_its_slot(
        self, make_stream, async_rf, rf
    ) -> None:
        slots = StreamSlots()
        stream, _ = make_stream(
            _Batches(), request=async_rf.get("/"), slot=slots.acquire(1)
        )
        with capture_signals(page_stream_closed) as recorder:
            stream.close()
        assert recorder.events == []
        assert slots.open == 0

    def test_the_async_opening_is_announced_from_the_loop(
        self, make_stream, async_rf, rf
    ) -> None:
        batches = _Batches(())
        stream, _ = make_stream(batches, request=async_rf.get("/"))
        batches.pinned = "sqlite"

        async def read() -> None:
            async for _ in stream:
                pass

        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            asyncio.run(read())
        stream.close()
        opened, closed = recorder.events
        assert opened.kwargs["request_lane"] == "sqlite"
        assert closed.kwargs["aborted"] is False


class TestReleaseConnections:
    """The request thread closes what no atomic block holds before the tail."""

    def test_only_a_connection_outside_an_atomic_block_closes(self) -> None:
        free = mock.Mock(in_atomic_block=False)
        held = mock.Mock(in_atomic_block=True)
        with mock.patch.object(connections, "all", return_value=[free, held]):
            release_connections()
        free.close_if_unusable_or_obsolete.assert_called_once_with()
        held.close_if_unusable_or_obsolete.assert_not_called()


def _fail_after_one(body: Iterator[bytes]) -> None:
    next(body)
    raise RuntimeError(_CONSUMER)


@pytest.fixture()
def held_stream(
    make_stream: Callable[..., tuple[StreamingPageResponse, list[Piece]]],
) -> Callable[..., tuple[StreamSlots, StreamingPageResponse]]:
    """Build a stream holding the second of two slots, so a double release shows."""

    def build(request: object = None) -> tuple[StreamSlots, StreamingPageResponse]:
        slots = StreamSlots()
        assert slots.acquire(100) is not None
        stream, _ = make_stream(
            _Batches((), ()), request=request, slot=slots.acquire(100)
        )
        assert slots.open == 2
        return slots, stream

    return build


class TestSlotReleasedOnce:
    """A stream owns the slot its view reserved and gives it back exactly once.

    A second slot held beside it shows a double release, which would free it too.
    """

    def test_a_finished_body(self, held_stream) -> None:
        slots, stream = held_stream()
        body = iter(stream)
        next(body)
        assert slots.open == 2
        list(body)
        assert slots.open == 1
        stream.close()
        assert slots.open == 1

    def test_a_body_that_raises(self, held_stream, gzip_request) -> None:
        slots, stream = held_stream(gzip_request)
        stream.settle()
        with (
            mock.patch.object(StreamEncoding, "write", side_effect=RuntimeError("w")),
            pytest.raises(RuntimeError, match="w"),
        ):
            next(iter(stream))
        assert slots.open == 1
        stream.close()
        assert slots.open == 1

    def test_a_close_before_the_first_write(self, held_stream) -> None:
        slots, stream = held_stream()
        stream.close()
        stream.close()
        assert list(stream) == []
        assert slots.open == 1

    def test_a_close_mid_stream(self, held_stream) -> None:
        slots, stream = held_stream()
        body = iter(stream)
        next(body)
        stream.close()
        del body, stream
        gc.collect()
        assert slots.open == 1

    def test_a_consumer_that_raises_and_drops_the_body(self, held_stream) -> None:
        slots, stream = held_stream()
        body = iter(stream)
        with pytest.raises(RuntimeError, match=_CONSUMER):
            _fail_after_one(body)
        assert slots.open == 2
        del body, stream
        gc.collect()
        assert slots.open == 1

    def test_a_response_dropped_unread(self, held_stream) -> None:
        slots, stream = held_stream()
        del stream
        gc.collect()
        assert slots.open == 1

    def test_a_cancelled_async_reader(self, held_stream, async_rf, rf) -> None:
        slots, stream = held_stream(async_rf.get("/"))
        taken: list[int] = []

        async def read() -> None:
            body = stream.__aiter__()
            await anext(body)
            taken.append(slots.open)
            task = asyncio.ensure_future(anext(body))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await body.aclose()

        asyncio.run(read())
        stream.close()
        assert taken == [2]
        assert slots.open == 1
