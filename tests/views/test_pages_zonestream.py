import asyncio
import json
import re
import zlib
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.http.response import HttpResponseBase
from django.test import AsyncClient, override_settings

from next.components.registry import DeferredComponents
from next.partial.headers import STREAM_ACCEPT
from next.suspense import PartialStream, page_stream_closed, page_stream_opened
from next.suspense.policy import StreamSlots, streaming
from next.testing import (
    NextClient,
    StreamedPage,
    aread_stream,
    boundary_errors,
    boundary_gate,
    capture_signals,
    parallel_boundaries,
)
from next.testing.boundaries import ComponentGate
from tests.support import routed, write_page
from tests.support.access import COMPONENTS
from tests.support.components import write_component
from tests.support.hosts import DOWN
from tests.support.streams import (
    after_opening,
    aread_releasing,
    at_heartbeat,
    read_releasing,
)


pytestmark = pytest.mark.django_db()


_PUBLIC = "x = 1\n"
_ZONES = "/zones/"
_LAYOUT = (
    "<!DOCTYPE html><html><head>{% collect_styles %}</head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
_SLOW = """
from next.components import component
from tests.support import hosts as host


@component.context("waited")
def waited() -> str:
    host.pause()
    return "yes"
"""
_HOSTILE = """
from next.components import component


@component.context("text")
def text() -> str:
    return "a\\nb\\r\\ndata: evil\\n\\nevent: end\\n\\u2028"
"""
_FAILING = """
from next.components import component


@component.context("value")
def value() -> str:
    raise ValueError("down")
"""
_CARD = """
from next.components import component

component.defer("load")
"""
_ZONED = (
    '<main>{% zone "a" %}<div>{% component "slow" n=1 %}{% component "quick" %}'
    '</div>{% endzone %}{% zone "b" %}<div>{% component "slow" n=2 %}</div>'
    '{% endzone %}{% zone "plain" %}<p>plain</p>{% endzone %}</main>'
)
_SSE = [
    {
        "BACKEND": "next.partial.JsonPartialProtocolBackend",
        "OPTIONS": {"SSE": {"HEARTBEAT_SECONDS": 0.05}},
    }
]


def _failing(slots: StreamSlots, seen: list[int]):
    """Return a step that notes the open streams, then raises."""

    def fail(*args: object, **kwargs: object) -> None:
        seen.append(slots.open)
        raise RuntimeError(DOWN)

    return fail


@pytest.fixture()
def pages(tmp_path: Path, fresh_deferred_components: DeferredComponents) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(_LAYOUT)
    write_component(root, "slow", module=_SLOW)
    write_component(root, "quick", module=None, body="<p>quick</p>")
    write_page(root, "zones", _PUBLIC, body=_ZONED)
    return root


def _headers(zones: str, **extra: str) -> dict[str, str]:
    return {"X-Next-Request": "1", "X-Next-Zone": zones, **extra}


def _streamed(zones: str) -> dict[str, str]:
    return _headers(zones, Accept=STREAM_ACCEPT)


async def _aread_releasing(
    url: str, headers: dict[str, str], gate: ComponentGate
) -> StreamedPage:
    with streaming():
        response = await AsyncClient().get(url, headers=headers)
    try:
        return await aread_releasing(response, gate, at_heartbeat)
    finally:
        await sync_to_async(response.close)()


def _stream(pages: Path, zones: str, url: str = _ZONES, **extra: object):
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        return NextClient().get_stream(url, headers=_headers(zones), **extra)


def _answer(
    pages: Path,
    zones: str,
    *,
    accept: str | None = STREAM_ACCEPT,
    threaded: bool = True,
    framework: dict[str, object] | None = None,
    **extra: object,
) -> HttpResponseBase:
    headers = {} if accept is None else {"Accept": accept}
    with (
        routed(pages, COMPONENT_BACKENDS=COMPONENTS, **(framework or {})),
        streaming() if threaded else nullcontext(),
    ):
        response = NextClient().get_zones(_ZONES, zones, headers=headers, **extra)
        if isinstance(response, PartialStream):
            b"".join(response.streaming_content)
            response.close()
    return response


def _varied(response: HttpResponseBase) -> set[str]:
    return {name.strip() for name in response.get("Vary", "").split(",")}


def _plain(html: str) -> str:
    """Return `html` without its wire ids and the state the stream settles."""
    html = re.sub(r"b-[0-9a-f]{8}-\d+", "b", html)
    return re.sub(r' data-next-state="[a-z]+"( aria-busy="true")?', "", html)


class TestNegotiation:
    """A zone GET streams only when every condition holds, else answers JSON."""

    def test_a_client_accepting_a_stream_gets_one(self, pages: Path) -> None:
        response = _answer(pages, "a")
        assert isinstance(response, PartialStream)
        assert response["Content-Type"] == "text/event-stream; charset=utf-8"
        assert "Accept" in _varied(response)

    @pytest.mark.parametrize(
        "accept",
        [None, "application/json", "text/event-stream;q=0, application/json", "*/*"],
    )
    def test_a_client_reading_no_stream_gets_json(
        self, pages: Path, accept: str | None
    ) -> None:
        response = _answer(pages, "a", accept=accept)
        assert not isinstance(response, PartialStream)
        assert response["Content-Type"] == "application/vnd.next.patches+json"
        assert "Accept" in _varied(response)

    def test_a_zone_without_deferred_jobs_answers_json(self, pages: Path) -> None:
        response = _answer(pages, "plain")
        assert not isinstance(response, PartialStream)
        assert "Accept" in _varied(response)

    def test_a_sync_worker_answers_json(self, pages: Path) -> None:
        response = _answer(pages, "a", threaded=False)
        assert not isinstance(response, PartialStream)
        assert json.loads(response.content)["ops"][0]["target"] == {"zone": "a"}

    def test_a_bot_answers_every_zone_inline(self, pages: Path) -> None:
        response = _answer(pages, "a,b", HTTP_USER_AGENT="Googlebot/2.1")
        assert not isinstance(response, PartialStream)
        html = "".join(op["html"] for op in json.loads(response.content)["ops"])
        assert html.count('data-next-state="ready"') == 3

    def test_stream_false_answers_json(self, pages: Path) -> None:
        framework = {"DEFER": {"POLICY": {"OPTIONS": {"STREAM": False}}}}
        response = _answer(pages, "a", framework=framework)
        assert not isinstance(response, PartialStream)

    def test_a_full_process_answers_json(self, pages: Path, slots: StreamSlots) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS, DEFER={"MAX_STREAMS": 1}),
            boundary_gate(component="slow") as gate,
            streaming(),
        ):
            held = NextClient().get_zones(
                _ZONES, "b", headers={"Accept": STREAM_ACCEPT}
            )
            next(iter(held.streaming_content))
            gate.release()
            overflow = NextClient().get_zones(
                _ZONES, "a", headers={"Accept": STREAM_ACCEPT}
            )
            held.close()
        assert isinstance(held, PartialStream)
        assert not isinstance(overflow, PartialStream)
        assert overflow.status_code == 200

    def test_a_post_answers_json_without_varying_on_accept(self, pages: Path) -> None:
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS), streaming():
            response = NextClient().post(
                _ZONES, data={}, headers=_headers("a", Accept=STREAM_ACCEPT)
            )
        assert not isinstance(response, PartialStream)
        assert "Accept" not in _varied(response)

    @override_settings(DEBUG=True)
    def test_a_partial_page_request_never_gets_a_document_stream(
        self, pages: Path
    ) -> None:
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS), streaming():
            response = NextClient().get(
                _ZONES, headers={"X-Next-Request": "1", "Accept": STREAM_ACCEPT}
            )
        assert response["X-Next-Stream"] == "inline (partial)"


class TestZoneStream:
    """A streamed zone GET writes its envelope, a patch per boundary, then the end."""

    def test_the_frames_arrive_in_completion_order(self, pages: Path) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            boundary_gate(component="slow") as gate,
            parallel_boundaries(),
            streaming(),
        ):
            response = NextClient().get(_ZONES, headers=_streamed("a"))
            page = read_releasing(response, gate, after_opening)
        assert page.hello == {"protocol": 2, "streamed": True}
        assert page.padded == 1024
        assert page.envelope is not None
        (op,) = page.envelope.ops
        assert (op.op, op.target) == ("morph", {"zone": "a"})
        assert op.html is not None
        assert op.html.count('data-next-state="loading" aria-busy="true"') == 2
        assert [chunk.html for chunk in page.chunks] == ["<p>quick</p>", "<p>1 yes</p>"]
        assert all(chunk.op == "inner" for chunk in page.chunks)
        assert page.end == "complete"
        assert (
            page.response["Cache-Control"]
            == "private, no-cache, no-store, no-transform"
        )

    def test_the_applied_zones_equal_the_json_answer(self, pages: Path) -> None:
        page = _stream(pages, "a,b")
        response = _answer(pages, "a,b", accept=None)
        ops = json.loads(response.content)["ops"]
        assert [op.target for op in page.envelope.ops] == [op["target"] for op in ops]
        whole = "".join(op["html"] for op in ops)
        assert _plain(page.final_html()) == _plain(whole)
        assert len(page.chunks) == 3

    def test_a_failing_boundary_settles_as_an_error_frame(self, pages: Path) -> None:
        write_component(
            pages, "failing", module=_FAILING, error="<p>failed {{ error.kind }}</p>"
        )
        write_page(
            pages,
            "failing",
            _PUBLIC,
            body='{% zone "f" %}<div>{% component "failing" %}</div>{% endzone %}',
        )
        with boundary_errors("render"):
            page = _stream(pages, "f", url="/failing/")
        (chunk,) = page.chunks
        assert (chunk.state, chunk.kind) == ("error", "error")
        assert chunk.html == "<p>failed error</p>"
        assert page.end == "complete"

    def test_the_deadline_settles_what_is_left_as_timeout(self, pages: Path) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS, DEFER={"DEADLINE": 0.2}),
            boundary_gate(component="slow"),
        ):
            page = NextClient().get_stream(_ZONES, headers=_headers("b"))
        (chunk,) = page.chunks
        assert (chunk.state, chunk.kind) == ("error", "timeout")
        assert page.end == "deadline"

    def test_hostile_text_never_breaks_the_framing(self, pages: Path) -> None:
        write_component(
            pages, "hostile", module=_HOSTILE, body="<pre>{{ text|safe }}</pre>"
        )
        write_page(
            pages,
            "hostile",
            _PUBLIC,
            body='{% zone "h" %}<div>{% component "hostile" %}</div>{% endzone %}',
        )
        page = _stream(pages, "h", url="/hostile/")
        body = b"".join(page.writes).decode()
        data = [line for line in body.split("\n") if line.startswith("data:")]
        assert len(data) == 4
        assert "\r" not in body
        expected = "<pre>a\nb\r\ndata: evil\n\nevent: end\n" + chr(0x2028) + "</pre>"
        assert page.chunks[0].html == expected
        assert page.end == "complete"

    def test_each_compressed_write_decodes_on_its_own(self, pages: Path) -> None:
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS), streaming():
            response = NextClient().get_zones(
                _ZONES,
                "a",
                headers={"Accept": STREAM_ACCEPT},
                HTTP_ACCEPT_ENCODING="gzip",
            )
            writes = list(response.streaming_content)
            response.close()
        decoder = zlib.decompressobj(31)
        texts = [decoder.decompress(write) for write in writes]
        assert response["Content-Encoding"] == "gzip"
        assert texts[0].startswith(b"event: hello\n")
        assert all(text.rstrip(b"\n").count(b"event: ") >= 1 for text in texts[:-1])
        assert texts[-1] == b""
        assert decoder.eof is True

    def test_a_page_opting_out_streams_plain(self, pages: Path) -> None:
        source = "from next.pages import page\n\npage.compress(False)\n"
        write_page(pages, "plainly", source, body=_ZONED)
        page = _stream(pages, "a", url="/plainly/", HTTP_ACCEPT_ENCODING="gzip")
        assert not page.response.has_header("Content-Encoding")
        assert page.end == "complete"

    def test_the_stream_is_announced_as_partial(self, pages: Path) -> None:
        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            _stream(pages, "a")
        opened, closed = recorder.events
        assert opened.kwargs["partial"] is True
        assert opened.kwargs["reason"] == "stream"
        assert opened.kwargs["boundaries"] == (("slow", "stream"), ("quick", "stream"))
        assert opened.kwargs["page_path"].name == "page.py"
        assert closed.kwargs["partial"] is True
        assert closed.kwargs["aborted"] is False
        assert (closed.kwargs["sent"], closed.kwargs["failed"]) == (2, 0)

    def test_a_held_job_lets_heartbeats_through_a_worker_thread(
        self, pages: Path
    ) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS, PARTIAL_BACKENDS=_SSE),
            boundary_gate(component="slow") as gate,
            parallel_boundaries(),
            streaming(),
        ):
            response = NextClient().get(_ZONES, headers=_streamed("b"))
            page = read_releasing(response, gate, at_heartbeat)
        pings = [write for write in page.writes if write == b": ping\n\n"]
        assert pings
        assert page.chunks[0].html == "<p>2 yes</p>"
        assert page.end == "complete"

    def test_a_reader_leaving_frees_the_slot_once(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            boundary_gate(component="slow"),
            streaming(),
            capture_signals(page_stream_closed) as recorder,
        ):
            response = NextClient().get_zones(
                _ZONES, "b", headers={"Accept": STREAM_ACCEPT}
            )
            writes = iter(response.streaming_content)
            first = next(writes)
            during = slots.open
            response.close()
        assert b"event: hello" in first
        assert during == 1
        assert slots.open == 0
        assert [event.kwargs["aborted"] for event in recorder.events] == [True]

    def test_a_stream_failing_after_its_render_frees_its_slot(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        seen: list[int] = []
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            streaming(),
            mock.patch(
                "next.views.pages.finish_zone_response",
                side_effect=_failing(slots, seen),
            ),
            pytest.raises(RuntimeError, match=DOWN) as raised,
        ):
            NextClient().get_zones(_ZONES, "b", headers={"Accept": STREAM_ACCEPT})
        assert raised.tb is not None
        assert seen == [1]
        assert slots.open == 0


class TestAsyncZoneStream:
    """Under ASGI the stream reads the same and keeps the reader alive while it waits."""

    def test_the_async_client_reads_the_stream(self, pages: Path) -> None:
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            page = asyncio.run(
                aread_stream(
                    AsyncClient(),
                    _ZONES,
                    headers=_headers("a,b", **{"Accept-Encoding": "gzip"}),
                )
            )
        page.response.close()
        assert page.response.is_async is True
        assert page.response["Content-Encoding"] == "gzip"
        assert len(page.chunks) == 3
        assert page.end == "complete"

    def test_a_held_job_lets_heartbeats_through(self, pages: Path) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS, PARTIAL_BACKENDS=_SSE),
            boundary_gate(component="slow") as gate,
        ):
            page = asyncio.run(_aread_releasing(_ZONES, _streamed("b"), gate))
        pings = [write for write in page.writes if write.startswith(b": ping\n\n")]
        assert pings
        assert page.chunks[0].html == "<p>2 yes</p>"
        assert page.end == "complete"


class TestBoundaryReloadStream:
    """A reload whose component holds a stream boundary streams it after the morph."""

    def test_the_morph_comes_first_then_the_nested_boundary(self, pages: Path) -> None:
        write_component(
            pages,
            "card",
            module=_CARD,
            body='<section>{% component "slow" n=5 %}</section>',
            loading="<i>card</i>",
        )
        write_page(pages, "card", _PUBLIC, body='<main>{% component "card" %}</main>')
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            client = NextClient()
            token = client.boundary_token("/card/", "card")
            headers = _headers("b-00000000-0", **{"X-Next-Boundary": token})
            page = client.get_stream("/card/", headers=headers)
            whole = client.get("/card/", headers=headers)
        assert page.envelope is not None
        morph = page.envelope.ops[0]
        assert (morph.op, morph.target) == ("morph", {"boundary": "b-00000000-0"})
        assert morph.extras == {"s": "ready"}
        assert 'data-next-state="loading"' in (morph.html or "")
        (chunk,) = page.chunks
        assert chunk.html == "<p>5 yes</p>"
        assert page.end == "complete"
        assert page.response["Referrer-Policy"] == "same-origin"
        assert (
            page.response["Cache-Control"]
            == "private, no-cache, no-store, no-transform"
        )
        assert "Accept" in _varied(whole)
        assert not isinstance(whole, PartialStream)

    def test_a_reload_stream_failing_after_its_render_frees_its_slot(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        write_component(
            pages,
            "card",
            module=_CARD,
            body='<section>{% component "slow" n=5 %}</section>',
            loading="<i>card</i>",
        )
        write_page(pages, "card", _PUBLIC, body='<main>{% component "card" %}</main>')
        seen: list[int] = []
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS), streaming():
            client = NextClient()
            token = client.boundary_token("/card/", "card")
            headers = _headers(
                "b-00000000-0", **{"X-Next-Boundary": token, "Accept": STREAM_ACCEPT}
            )
            with (
                mock.patch(
                    "next.suspense.reload.private", side_effect=_failing(slots, seen)
                ),
                pytest.raises(RuntimeError, match=DOWN) as raised,
            ):
                client.get("/card/", headers=headers)
        assert raised.tb is not None
        assert seen == [1]
        assert slots.open == 0


class TestStreamMarker:
    """A page tells the runtime whether its partial requests may stream."""

    def test_a_threaded_server_marks_the_payload(self, pages: Path) -> None:
        write_page(pages, "flat", _PUBLIC, body='{% zone "q" %}<p>q</p>{% endzone %}')
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            # A render that calls a component loads the registry that knows one defers.
            NextClient().get(_ZONES)
            with streaming():
                threaded = NextClient().get("/flat/").content.decode()
            synced = NextClient().get("/flat/").content.decode()
        assert '"$stream":true' in threaded
        assert '"$stream"' not in synced
        reader = '<script async fetchpriority="low" src="/static/next/next.reader.min'
        assert reader in threaded
        assert "next.reader" not in synced
