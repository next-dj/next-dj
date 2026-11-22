import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from django.http import HttpResponse, StreamingHttpResponse
from django.test import AsyncClient, Client, override_settings

from next.compression import GzipBackend
from next.partial.headers import STREAM_ACCEPT
from next.testing import (
    NextClient,
    SseEvent,
    StreamedPage,
    boundary_errors,
    boundary_gate,
    sse_events,
)
from next.testing.boundaries import recorded_failures
from next.testing.streaming import _accepting, aread_stream, read_stream
from tests.support import routed, write_page
from tests.support.access import COMPONENTS


_SHELL = (
    '<html><head></head><body><div id="b-1" data-next-boundary="b-1" '
    'data-next-boundary-key="k1" data-next-state="loading" aria-busy="true">'
    '<div>wait</div></div><table><tr id="b-2" data-next-boundary="b-2" '
    'data-next-boundary-key="k2" data-next-state="loading" aria-busy="true">'
    "<td>wait</td></tr></table>"
)


def _script(
    payload: object, template: str = "<script>(self.$nx||=[]).push({})</script>"
) -> bytes:
    return template.format(json.dumps(payload)).encode()


def _chunk(ident: str, op: str, html: str, **wrapper: object) -> dict[str, object]:
    ops: list[dict[str, object]] = [
        {"op": op, "target": {"boundary": ident}, "html": html}
    ]
    ops.extend(wrapper.pop("extra", []))
    envelope = {"version": "1", "ops": ops, "assets": [], "form": None}
    return {"z": ident, "s": "ready", **wrapper, "e": envelope}


def _page(
    *chunks: dict[str, object], shell: str = _SHELL, names: dict[str, str] | None = None
) -> StreamedPage:
    pad = b"<!--" + b" " * 8 + b"-->"
    writes = [shell.encode() + pad, *(_script(chunk) for chunk in chunks)]
    writes.append(_script(0) + b"</body></html>")
    return StreamedPage(writes, HttpResponse(), names=names)


class TestStreamedPage:
    """A streamed body splits into its shell, its chunks and the tail."""

    def test_the_shell_drops_its_padding_and_the_tail_follows_the_end(self) -> None:
        page = _page()
        assert page.shell == _SHELL
        assert page.ended is True
        assert page.cut is False
        assert page.tail == "</body></html>"
        assert page.chunks == []

    def test_an_inner_chunk_settles_its_wrapper_state(self) -> None:
        page = _page(_chunk("b-1", "inner", "<p>done</p>"))
        assert page.chunks[0].op == "inner"
        assert (
            '<div id="b-1" data-next-boundary="b-1" data-next-boundary-key="k1" '
            'data-next-state="ready"><p>done</p></div>'
        ) in page.final_html()

    def test_an_error_chunk_names_its_kind(self) -> None:
        chunk = _chunk("b-1", "inner", "<p>x</p>", s="error", k="timeout")
        html = _page(chunk).final_html()
        assert 'data-next-state="error" data-next-error="timeout"><p>x</p>' in html

    def test_an_error_chunk_sets_its_digest_on_the_wrapper(self) -> None:
        chunk = _chunk("b-1", "inner", "<p>x</p>", s="error", k="error", g="d1g")
        page = _page(chunk)
        assert page.chunks[0].digest == "d1g"
        assert (
            'data-next-state="error" data-next-error="error" '
            'data-next-error-digest="d1g"><p>x</p>'
        ) in page.final_html()

    def test_a_replace_chunk_takes_the_whole_element(self) -> None:
        row = '<tr id="b-2" data-next-state="ready"><td>row</td></tr>'
        html = _page(_chunk("b-2", "replace", row)).final_html()
        assert f"<table>{row}</table>" in html

    def test_a_meta_op_adds_its_robots_tag(self) -> None:
        meta = {"op": "meta", "robots": "noindex"}
        chunk = _chunk("b-1", "inner", "", k="not_found", extra=[meta])
        html = _page(chunk).final_html()
        assert '<meta name="robots" content="noindex"></head>' in html

    def test_a_chunk_without_a_target_is_dropped(self) -> None:
        page = _page(_chunk("b-9", "inner", "<p>lost</p>"))
        assert page.final_html() == _SHELL + "</body></html>"

    def test_an_unclosed_wrapper_is_left_alone(self) -> None:
        shell = '<div id="b-1" data-next-state="loading" aria-busy="true">'
        page = _page(_chunk("b-1", "inner", "<p>x</p>"), shell=shell)
        assert page.final_html().startswith(shell)

    def test_a_boundary_is_found_by_its_id_or_its_key(self) -> None:
        page = _page(_chunk("b-1", "inner", "<p>a</p>"), _chunk("b-2", "inner", ""))
        assert page.boundary("b-1").html == "<p>a</p>"
        assert page.boundary("k2").id == "b-2"
        with pytest.raises(LookupError, match="no chunk settles the boundary 'k9'"):
            page.boundary("k9")

    def test_a_boundary_is_found_by_its_name(self) -> None:
        page = _page(
            _chunk("b-1", "inner", "<p>a</p>"),
            _chunk("b-2", "inner", "<p>b</p>"),
            names={"b-1": "card", "b-2": "pages/slow/loading.djx"},
        )
        assert page.boundary("card").id == "b-1"
        assert page.boundary("pages/slow/loading.djx").id == "b-2"

    def test_an_id_or_a_key_wins_over_a_name(self) -> None:
        page = _page(
            _chunk("b-1", "inner", "<p>a</p>"),
            _chunk("b-2", "inner", "<p>b</p>"),
            names={"b-1": "k2", "b-2": "card"},
        )
        assert page.boundary("k2").id == "b-2"

    def test_a_name_two_chunks_share_lists_both(self) -> None:
        page = _page(
            _chunk("b-1", "inner", "<p>a</p>"),
            _chunk("b-2", "inner", "<p>b</p>"),
            names={"b-1": "card", "b-2": "card"},
        )
        with pytest.raises(
            LookupError, match="2 chunks settle a boundary named"
        ) as caught:
            page.boundary("card")
        assert "b-1 with key 'k1', b-2 with key 'k2'" in str(caught.value)

    def test_a_page_read_by_hand_knows_no_names(self) -> None:
        page = _page(_chunk("b-1", "inner", "<p>a</p>"))
        with pytest.raises(LookupError, match="no chunk settles the boundary 'card'"):
            page.boundary("card")

    def test_an_empty_body_has_no_shell(self) -> None:
        page = StreamedPage([], HttpResponse())
        assert (page.shell, page.padded, page.ended) == ("", 0, False)
        assert page.chunks == []

    def test_a_body_without_its_end_marker_is_cut(self) -> None:
        writes = [_SHELL.encode(), _script(_chunk("b-1", "inner", "<p>a</p>"))]
        page = StreamedPage(writes, StreamingHttpResponse(iter(())))
        assert [chunk.id for chunk in page.chunks] == ["b-1"]
        assert (page.ended, page.end, page.cut) == (False, None, True)


class TestDecodedAndCustomStreams:
    """A compressed or custom-templated stream reads like the default one."""

    def test_a_gzip_stream_is_decoded_write_by_write(self) -> None:
        compressor = GzipBackend().create(4, None)
        plain = [
            _SHELL.encode() + b"<!--" + b" " * 8 + b"-->",
            _script(_chunk("b-1", "inner", "<p>done</p>")),
            _script(0) + b"</body></html>",
        ]
        writes = [
            compressor.compress(part + b"<!--Ab9._-->") + compressor.flush()
            for part in plain
        ]
        writes[-1] += compressor.finish()
        response = StreamingHttpResponse(iter(()))
        response["Content-Encoding"] = "gzip"
        page = StreamedPage(writes, response)
        assert page.shell == _SHELL
        assert page.tail == "</body></html>"
        assert page.boundary("k1").html == "<p>done</p>"

    def test_a_custom_template_pushing_to_nx_is_read(self) -> None:
        custom = "<script nonce='n' type='module'>self.$nx.push({});</script>"
        writes = [
            _SHELL.encode(),
            _script(_chunk("b-1", "inner", "<p>a</p>"), custom),
            b"<script>console.log(1)</script>",
            _script(0, custom) + b"</body></html>",
        ]
        page = StreamedPage(writes, StreamingHttpResponse(iter(())))
        assert [chunk.id for chunk in page.chunks] == ["b-1"]
        assert page.ended is True

    def test_a_stream_without_a_push_is_refused(self) -> None:
        writes = [_SHELL.encode(), b"<script>q.add({})</script></body></html>"]
        with pytest.raises(AssertionError, match="no chunk and no end marker"):
            StreamedPage(writes, StreamingHttpResponse(iter(())))


def test_a_plain_answer_is_refused_with_its_reason() -> None:
    response = HttpResponse("x")
    response["X-Next-Stream"] = "inline (bot)"

    class _Client:
        def get(self, url: str, **extra: object) -> HttpResponse:
            return response

    with pytest.raises(AssertionError, match=r"answered 200 without a stream inline"):
        read_stream(_Client(), "/x/")


def _event(name: str, data: object) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def _partial(*writes: bytes) -> StreamedPage:
    response = HttpResponse(content_type="text/event-stream; charset=utf-8")
    return StreamedPage(list(writes), response)


class TestStreamedPartial:
    """An event stream reads into its hello, its envelope, its frames and its end."""

    def test_the_frames_fill_the_page(self) -> None:
        envelope = {
            "version": "1",
            "ops": [
                {"op": "morph", "target": {"zone": "z"}, "html": _SHELL},
                {"op": "context", "target": None},
            ],
            "assets": [],
            "form": None,
        }
        page = _partial(
            _event("hello", {"protocol": 2, "streamed": True}),
            _event("patch", {"e": envelope}) + b": " + b" " * 8 + b"\n\n",
            b": ping\n\n",
            _event("patch", _chunk("b-1", "inner", "<p>ok</p>")),
            _event("end", {"code": "complete"}),
        )
        assert page.hello == {"protocol": 2, "streamed": True}
        assert page.envelope is not None
        assert page.shell == _SHELL
        assert [chunk.id for chunk in page.chunks] == ["b-1"]
        assert (page.ended, page.end, page.cut) == (True, "complete", False)
        assert '<div id="b-1" data-next-boundary="b-1"' in page.final_html()
        assert 'data-next-state="ready"><p>ok</p></div>' in page.final_html()
        assert page.boundary("k1").html == "<p>ok</p>"
        assert page.boundary("k1").digest is None

    def test_an_error_frame_reads_its_digest(self) -> None:
        envelope = {"version": "1", "ops": [], "assets": [], "form": None}
        page = _partial(
            _event("patch", {"e": envelope}),
            _event("patch", _chunk("b-1", "inner", "", s="error", k="error", g="d")),
        )
        (chunk,) = page.chunks
        assert (chunk.state, chunk.kind, chunk.digest) == ("error", "error", "d")

    def test_a_stream_cut_before_its_envelope_holds_no_shell(self) -> None:
        page = _partial(_event("hello", {"protocol": 2, "streamed": True}))
        assert (page.envelope, page.shell, page.end) == (None, "", None)
        assert (page.ended, page.cut) == (False, True)

    def test_a_frame_cut_mid_way_marks_the_stream_cut(self) -> None:
        envelope = {"version": "1", "ops": [], "assets": [], "form": None}
        whole = _event("patch", _chunk("b-1", "inner", "<p>ok</p>"))
        page = _partial(_event("patch", {"e": envelope}), whole[: len(whole) // 2])
        assert page.chunks == []
        assert (page.ended, page.end, page.cut) == (False, None, True)

    def test_an_end_frame_cut_mid_way_is_no_end(self) -> None:
        envelope = {"version": "1", "ops": [], "assets": [], "form": None}
        end = _event("end", {"code": "complete"})
        page = _partial(_event("patch", {"e": envelope}), end[:-2])
        assert (page.ended, page.end, page.cut) == (False, None, True)


class TestEvents:
    """The SSE reader splits events as a `fetch` reader does."""

    def test_comments_unknown_fields_and_empty_blocks_are_skipped(self) -> None:
        body = (
            "retry: 10\n\n: comment\n\nevent: one\ndata: a\r\ndata:b\r\n\r\n"
            "id: 7\rdata: " + chr(0x2028) + "\n\n"
        )
        assert sse_events(body) == (
            [SseEvent("one", "a\nb"), SseEvent("message", chr(0x2028), "7")],
            False,
        )

    @pytest.mark.parametrize(
        ("tail", "cut"),
        [
            pytest.param("", False, id="whole"),
            pytest.param(": ping\n", False, id="comment"),
            pytest.param("event: two\ndata: x\n", True, id="unended"),
        ],
    )
    def test_a_body_that_stops_inside_an_event_is_cut(self, tail: str, cut) -> None:
        events, stopped = sse_events("data: a\n\n" + tail)
        assert (events, stopped) == ([SseEvent("message", "a")], cut)


class TestAccepting:
    """A partial request read as a stream asks for one unless it names its own Accept."""

    def test_a_partial_request_asks_for_a_stream(self) -> None:
        sent = _accepting({"headers": {"X-Next-Request": "1"}})
        assert sent["headers"] == {"X-Next-Request": "1", "Accept": STREAM_ACCEPT}

    def test_a_partial_request_through_meta_asks_too(self) -> None:
        sent = _accepting({"HTTP_X_NEXT_REQUEST": "1"})
        assert sent["headers"] == {"Accept": STREAM_ACCEPT}

    @pytest.mark.parametrize(
        "extra",
        [
            {},
            {"headers": {"Accept": "application/json", "X-Next-Request": "1"}},
            {"HTTP_X_NEXT_REQUEST": "1", "HTTP_ACCEPT": "application/json"},
        ],
    )
    def test_a_document_or_a_named_accept_is_kept(self, extra: dict) -> None:
        assert _accepting(extra) == extra


_LAYOUT = (
    "<!DOCTYPE html><html><head>{% collect_styles %}</head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)


@pytest.fixture()
def gated_pages(tmp_path: Path) -> Path:
    root = tmp_path / "pages"
    folder = root / "_components" / "card"
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text("<p>card done</p>")
    (folder / "loading.djx").write_text("<p>card loading</p>")
    (root / "layout.djx").write_text(_LAYOUT)
    write_page(root, "one", "x = 1\n", body="<main>{% component 'card' %}</main>")
    write_page(root, "slow", "x = 1\n", body="<main>slow done</main>")
    (root / "slow" / "loading.djx").write_text("<p>slow loading</p>")
    return root


@pytest.mark.django_db()
class TestGatedStream:
    """A gated boundary keeps its loading UI in the shell, its chunk brings content."""

    def _read(self, root: Path, url: str, **gate: object) -> tuple[StreamedPage, bool]:
        with (
            routed(root, COMPONENT_BACKENDS=COMPONENTS, DEFER={"DEADLINE": 2}),
            boundary_gate(**gate, after_shell=True) as held,
        ):
            page = read_stream(Client(), url)
        return page, held.entered.is_set()

    def test_a_gated_component_streams_after_its_loading_shell(
        self, gated_pages: Path
    ) -> None:
        page, entered = self._read(gated_pages, "/one/", component="card")
        assert entered is True
        assert "<p>card loading</p>" in page.shell
        assert "card done" not in page.shell
        (chunk,) = page.chunks
        assert chunk.state == "ready"
        assert "<p>card done</p>" in chunk.html

    def test_a_gated_page_boundary_streams_after_its_loading_shell(
        self, gated_pages: Path
    ) -> None:
        page, entered = self._read(gated_pages, "/slow/", page=gated_pages / "slow")
        assert entered is True
        assert "<p>slow loading</p>" in page.shell
        assert "slow done" not in page.shell
        (chunk,) = page.chunks
        assert chunk.state == "ready"
        assert "slow done" in chunk.html

    def test_the_async_reader_releases_the_gate_after_the_shell(
        self, gated_pages: Path
    ) -> None:
        with (
            routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS, DEFER={"DEADLINE": 2}),
            boundary_gate(component="card", after_shell=True),
        ):
            page = asyncio.run(aread_stream(AsyncClient(), "/one/"))
        assert "<p>card loading</p>" in page.shell
        assert page.chunks[0].state == "ready"

    def test_a_chunk_is_found_by_its_component_name(self, gated_pages: Path) -> None:
        page, _entered = self._read(gated_pages, "/one/", component="card")
        assert page.boundary("card") is page.chunks[0]

    def test_a_chunk_is_found_by_its_page_boundary_name(
        self, gated_pages: Path
    ) -> None:
        with override_settings(BASE_DIR=gated_pages.parent):
            page, _entered = self._read(gated_pages, "/slow/", page="pages/slow")
        assert page.boundary("pages/slow/loading.djx") is page.chunks[0]

    def test_the_async_reader_finds_a_chunk_by_name(self, gated_pages: Path) -> None:
        with routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS):
            page = asyncio.run(aread_stream(AsyncClient(), "/one/"))
        assert page.boundary("card") is page.chunks[0]

    def test_two_calls_of_one_component_are_ambiguous_by_name(
        self, gated_pages: Path
    ) -> None:
        body = "<main>{% component 'card' %}{% component 'card' %}</main>"
        write_page(gated_pages, "two", "x = 1\n", body=body)
        with routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS):
            page = NextClient().get_stream("/two/")
        first, second = page.chunks
        with pytest.raises(LookupError, match="2 chunks settle") as caught:
            page.boundary("card")
        assert first.id in str(caught.value)
        assert second.id in str(caught.value)

    def test_a_plain_gate_holds_past_the_shell_until_the_deadline(
        self, gated_pages: Path
    ) -> None:
        with (
            routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS, DEFER={"DEADLINE": 0.2}),
            boundary_gate(component="card"),
        ):
            page = read_stream(Client(), "/one/")
        assert (page.chunks[0].state, page.chunks[0].kind) == ("error", "timeout")

    @pytest.mark.parametrize("gate", [{}, {"component": "card", "page": "slow"}])
    def test_a_gate_names_exactly_one_boundary(self, gate: dict) -> None:
        with pytest.raises(TypeError, match="exactly one"), boundary_gate(**gate):
            pass

    def test_a_gate_on_a_directory_without_a_loading_djx_is_refused(
        self, gated_pages: Path
    ) -> None:
        with (
            routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS),
            pytest.raises(LookupError, match=r"no loading\.djx"),
            boundary_gate(page=gated_pages / "one"),
        ):
            pass

    def test_a_relative_page_reads_from_base_dir(self, gated_pages: Path) -> None:
        with (
            routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS),
            override_settings(BASE_DIR=gated_pages.parent),
            boundary_gate(page="pages/slow") as held,
        ):
            assert held.name == "pages/slow/loading.djx"

    def test_a_gate_on_an_unknown_component_is_refused(self, gated_pages: Path) -> None:
        with (
            routed(gated_pages, COMPONENT_BACKENDS=COMPONENTS),
            pytest.raises(LookupError, match="'crad'"),
            boundary_gate(component="crad"),
        ):
            pass


_FAILING_PAGE = """from next.pages import context


@context("thing", guard=False)
def thing() -> str:
    raise ValueError("down")
"""


@pytest.mark.django_db()
def test_a_page_boundary_failure_names_its_streamed_wrapper(tmp_path: Path) -> None:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(_LAYOUT)
    write_page(root, "down", _FAILING_PAGE, body="<main>{{ thing }}</main>")
    (root / "down" / "loading.djx").write_text("<p>wait</p>")
    (root / "down" / "error.djx").write_text("<p>page failed</p>")
    with (
        routed(root, COMPONENT_BACKENDS=COMPONENTS),
        boundary_errors("render"),
        recorded_failures() as failures,
    ):
        page = read_stream(Client(), "/down/")
    (failure,) = failures
    (chunk,) = page.chunks
    assert failure.addressing == "page"
    assert failure.id == chunk.id
    assert f'id="{failure.id}"' in page.shell


_TRIP_FAILING = """from next.components import component

component.defer("load", props=("sku",))


@component.context("thing")
def thing(sku: str) -> str:
    raise ValueError(sku)
"""


@pytest.mark.django_db()
def test_a_reloaded_failure_names_the_wrapper_it_morphs(tmp_path: Path) -> None:
    root = tmp_path / "pages"
    folder = root / "_components" / "card"
    folder.mkdir(parents=True)
    (folder / "component.py").write_text(_TRIP_FAILING)
    (folder / "component.djx").write_text("<p>{{ thing }}</p>")
    (folder / "error.djx").write_text("<p>card failed</p>")
    (root / "layout.djx").write_text(_LAYOUT)
    write_page(root, "shop", "x = 1\n", body='{% component "card" sku="a" %}')
    with (
        routed(root, COMPONENT_BACKENDS=COMPONENTS),
        boundary_errors("render"),
        recorded_failures() as failures,
    ):
        envelope = NextClient().get_boundary("/shop/", "card", sku="b")
    (failure,) = failures
    (op,) = envelope.ops
    assert op["target"] == {"boundary": failure.id}


def _down(tmp_path: Path) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(_LAYOUT)
    write_page(root, "down", _FAILING_PAGE, body="<main>{{ thing }}</main>")
    (root / "down" / "loading.djx").write_text("<p>wait</p>")
    (root / "down" / "error.djx").write_text("<p>page failed</p>")
    return root


def _plain(url: str) -> StreamedPage:
    return read_stream(Client(), url)


def _next(url: str) -> StreamedPage:
    return NextClient().get_stream(url)


def _async(url: str) -> StreamedPage:
    return asyncio.run(aread_stream(AsyncClient(), url))


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "reader", [_plain, _next, _async], ids=["read_stream", "get_stream", "aread"]
)
def test_a_chunk_failure_reaches_every_enclosing_block_once(
    tmp_path: Path, reader: Callable[[str], StreamedPage]
) -> None:
    with (
        routed(_down(tmp_path), COMPONENT_BACKENDS=COMPONENTS),
        boundary_errors("render"),
        recorded_failures() as outer,
        recorded_failures() as inner,
    ):
        page = reader("/down/")
    (chunk,) = page.chunks
    assert [failure.id for failure in outer] == [chunk.id]
    assert outer == inner


@pytest.mark.django_db()
def test_get_stream_keeps_the_chunk_failure_on_the_response(tmp_path: Path) -> None:
    with (
        routed(_down(tmp_path), COMPONENT_BACKENDS=COMPONENTS),
        boundary_errors("render"),
        recorded_failures() as outer,
    ):
        page = NextClient().get_stream("/down/")
    assert outer == list(page.response.boundary_failures)
