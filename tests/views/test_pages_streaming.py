import asyncio
import re
import zlib
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.test import AsyncClient, override_settings

from next.components.registry import DeferredComponents
from next.suspense import (
    StreamingPageResponse,
    boundary_rendered,
    page_stream_closed,
    page_stream_opened,
)
from next.suspense.policy import StreamSlots, streaming
from next.suspense.response import Chunks
from next.testing import (
    NextClient,
    aread_stream,
    boundary_errors,
    boundary_gate,
    capture_signals,
    override_next_settings,
    parallel_boundaries,
)
from tests.support import routed, write_page
from tests.support.access import COMPONENTS
from tests.support.components import write_component
from tests.support.hosts import DOWN, NONCE_VALUE
from tests.support.streams import after_opening, read_releasing


pytestmark = pytest.mark.django_db()


_PUBLIC = "x = 1\n"
_BOOM = "tests.support.hosts.Boom"
_NONCE = "tests.support.hosts.OwnNonce"
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
_RAISING = """
from django.core.exceptions import SuspiciousOperation
from django.http import Http404

from next.components import component


@component.context("value")
def value() -> str:
    raise {error}
"""
_EVIL = """
from next.components import component


@component.context("text")
def text() -> str:
    return "</script><!--<script>alert(1)</script>\\u2028\\u2029"
"""
_MISSING_BODY = """
from django.http import Http404

from next.pages import context


@context("thing", guard=False)
def thing() -> str:
    raise Http404
"""


@pytest.fixture()
def pages(tmp_path: Path, fresh_deferred_components: DeferredComponents) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(_LAYOUT)
    write_component(root, "slow", module=_SLOW, css=True)
    return root


@pytest.fixture()
def one_page(pages: Path) -> Path:
    """Write the page `/one/` holding the slow component."""
    return write_page(pages, "one", _PUBLIC, body="<main>{% component 'slow' %}</main>")


def _stream(pages: Path, url: str, **extra: object):
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        return NextClient().get_stream(url, **extra)


def _plain(html: str) -> str:
    """Return `html` without what only differs between two renders of one page."""
    html = re.sub(r"<!--/?b-[0-9a-f]{8}-\d+:?[a-z]*-->", "", html)
    html = re.sub(r"b-[0-9a-f]{8}-", "b-", html)
    html = re.sub(r'"token":"[^"]+"', "", html)
    html = re.sub(r"<link[^>]*>\n?", "", html)
    html = html.replace(" data-next-streamed", "")
    html = re.sub(
        r'<script async src="[^"]*next\.reader\.min\.js"></script>\n', "", html
    )
    html = html.replace(',"$stream":true', "")
    return re.sub(r"<noscript>.*?</noscript>", "", html)


class TestStreamedPage:
    """A page with deferred calls writes its shell, then a chunk per boundary."""

    @pytest.mark.usefixtures("one_page")
    def test_the_shell_arrives_before_any_job_finished(self, pages: Path) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            capture_signals(boundary_rendered) as recorder,
            boundary_gate(component="slow") as gate,
            streaming(),
        ):
            response = NextClient().get("/one/")
            writes = iter(response.streaming_content)
            shell = next(writes)
            settled_before = len(recorder.events)
            gate.release()
            rest = b"".join(writes)
            response.close()
        assert isinstance(response, StreamingPageResponse)
        assert b'data-next-state="loading" aria-busy="true"' in shell
        assert b"<p>loading</p>" in shell
        assert settled_before == 0
        assert b"yes" in rest

    @pytest.mark.usefixtures("one_page")
    def test_the_shell_declares_the_reader_at_once(self, pages: Path) -> None:
        page = _stream(pages, "/one/")
        reader = '<script async src="/static/next/next.reader.min.js"></script>\n'
        assert reader in page.shell
        assert page.shell.index(reader) < page.shell.index("Next._init(")

    def test_chunks_arrive_in_completion_order(self, pages: Path) -> None:
        write_component(pages, "quick", module=None, body="<p>quick</p>")
        write_page(
            pages,
            "two",
            _PUBLIC,
            body="<main>{% component 'slow' n=1 %}{% component 'quick' %}</main>",
        )
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            boundary_gate(component="slow") as gate,
            parallel_boundaries(),
            streaming(),
        ):
            response = NextClient().get("/two/")
            page = read_releasing(response, gate, after_opening)
        assert [chunk.html for chunk in page.chunks] == ["<p>quick</p>", "<p>1 yes</p>"]
        assert page.ended is True

    @pytest.mark.usefixtures("one_page")
    def test_each_chunk_carries_an_envelope(self, pages: Path) -> None:
        (chunk,) = _stream(pages, "/one/").chunks
        assert chunk.state == "ready"
        assert chunk.kind is None
        assert chunk.op == "inner"
        assert chunk.envelope.ops[0].target == {"boundary": chunk.id}
        assert chunk.envelope.version

    def test_the_applied_page_equals_the_assembled_one(self, pages: Path) -> None:
        write_component(
            pages,
            "outer",
            module=None,
            body="<section>{% component 'slow' n=9 %}</section>",
        )
        write_page(
            pages,
            "mixed",
            _PUBLIC,
            body="<main>{% component 'outer' %}{% component 'slow' n=3 %}</main>",
        )
        streamed = _stream(pages, "/mixed/")
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            assembled = NextClient().get("/mixed/")
        assert not isinstance(assembled, StreamingPageResponse)
        assert _plain(streamed.final_html()) == _plain(assembled.content.decode())

    def test_script_breaking_text_stays_inside_the_payload(self, pages: Path) -> None:
        text = "</script><!--<script>alert(1)</script>" + chr(0x2028) + chr(0x2029)
        write_component(pages, "evil", module=_EVIL, body="<p>{{ text|safe }}</p>")
        write_page(pages, "evil", _PUBLIC, body="<main>{% component 'evil' %}</main>")
        page = _stream(pages, "/evil/")
        written = b"".join(page.writes[1:]).decode()
        payload = written[: written.index("push(0)")].rpartition("<script")[0]
        assert payload.count("</script>") == payload.count("<script")
        assert "<!--<" not in payload
        assert chr(0x2028) not in payload
        assert chr(0x2029) not in payload
        assert page.chunks[0].html == f"<p>{text}</p>"

    def test_a_nested_boundary_follows_its_parent_and_names_it(
        self, pages: Path
    ) -> None:
        write_component(
            pages,
            "outer",
            module=None,
            body="<section>{% component 'slow' n=9 %}</section>",
        )
        write_page(pages, "nest", _PUBLIC, body="<main>{% component 'outer' %}</main>")
        page = _stream(pages, "/nest/")
        parent, child = page.chunks
        assert parent.parent is None
        assert child.parent is not None
        assert page.boundary(child.parent) is parent
        assert "<!--" not in parent.html
        assert child.html == "<p>9 yes</p>"

    def test_an_adopted_root_is_replaced(self, pages: Path) -> None:
        write_component(
            pages,
            "row",
            module=None,
            body="<tr><td>{{ n }}</td></tr>",
            loading="<tr><td>wait</td></tr>",
        )
        write_page(
            pages,
            "table",
            _PUBLIC,
            body="<table><tbody>{% component 'row' n=4 %}</tbody></table>",
        )
        page = _stream(pages, "/table/")
        (chunk,) = page.chunks
        assert chunk.op == "replace"
        assert chunk.html.startswith(f'<tr id="{chunk.id}"')
        assert "<td>4</td>" in page.final_html()

    def test_an_erroring_job_settles_as_an_error(self, pages: Path) -> None:
        write_component(
            pages,
            "broken",
            module=_RAISING.format(error='ValueError("down")'),
            error="<p>broken {{ error.kind }}</p>",
        )
        write_page(pages, "broken", _PUBLIC, body="{% component 'broken' %}")
        with boundary_errors("render"):
            (chunk,) = _stream(pages, "/broken/").chunks
        assert (chunk.state, chunk.kind) == ("error", "error")
        assert chunk.html == "<p>broken error</p>"

    def test_a_job_past_its_timeout_settles_as_timeout(self, pages: Path) -> None:
        write_page(
            pages, "late", _PUBLIC, body="{% component 'slow' n=1 timeout='50ms' %}"
        )
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            boundary_gate(component="slow"),
            parallel_boundaries(),
        ):
            (chunk,) = NextClient().get_stream("/late/").chunks
        assert (chunk.state, chunk.kind) == ("error", "timeout")

    @pytest.mark.parametrize(
        ("error", "kind"),
        [
            ("Http404", "not_found"),
            ('SuspiciousOperation("tampered")', "bad_request"),
            ('ValueError("down")', "error"),
        ],
    )
    def test_an_exception_after_the_first_byte_maps_to_its_kind(
        self, pages: Path, error: str, kind: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_component(pages, "raising", module=_RAISING.format(error=error))
        write_page(pages, "raising", _PUBLIC, body="{% component 'raising' %}")
        with boundary_errors("render"), caplog.at_level("ERROR"):
            page = _stream(pages, "/raising/")
        assert page.response.status_code == 200
        (chunk,) = page.chunks
        assert (chunk.state, chunk.kind) == ("error", kind)
        security = [r for r in caplog.records if r.name.startswith("django.security")]
        assert [r.name for r in security] == (
            ["django.security.SuspiciousOperation"] if kind == "bad_request" else []
        )

    def test_a_missing_page_body_adds_noindex(self, pages: Path) -> None:
        section = pages / "shop"
        section.mkdir()
        (section / "loading.djx").write_text("<p>loading shop</p>")
        (section / "layout.djx").write_text("<section>{% template %}</section>")
        write_page(section, "item", _MISSING_BODY, body="<b>{{ thing }}</b>")
        page = _stream(pages, "/shop/item/")
        (chunk,) = page.chunks
        assert page.response.status_code == 200
        assert chunk.kind == "not_found"
        assert chunk.envelope.ops[-1].as_dict() == {"op": "meta", "robots": "noindex"}
        assert '<meta name="robots" content="noindex">' in page.final_html()

    def test_the_ledger_sends_a_shared_stylesheet_once(self, pages: Path) -> None:
        write_component(
            pages,
            "outer",
            module=None,
            body="<section>{% component 'inner' %}{% component 'inner' %}</section>",
            css=True,
        )
        write_component(pages, "inner", module=None, body="<i>in</i>", css=True)
        write_page(
            pages,
            "assets",
            _PUBLIC,
            body="<main>{% component 'outer' %}{% component 'slow' %}</main>",
        )
        page = _stream(pages, "/assets/")
        sent = [asset.url for chunk in page.chunks for asset in chunk.envelope.assets]
        assert sent == ["/static/next/components/inner.css"]
        assert "outer.css" in page.shell
        assert "slow.css" in page.shell

    def test_a_small_shell_is_padded_and_the_end_comes_with_the_tail(
        self, pages: Path
    ) -> None:
        (pages / "layout.djx").write_text("<body>{% template %}</body>")
        write_page(pages, "small", _PUBLIC, body="{% component 'slow' %}")
        page = _stream(pages, "/small/")
        assert page.padded == 1024
        assert page.writes[-1].rstrip().endswith(b"push(0)</script></body>")
        assert page.tail.strip() == "</body>"

    @pytest.mark.usefixtures("one_page")
    def test_a_machinery_failure_still_writes_the_end_and_the_tail(
        self, pages: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            mock.patch.object(Chunks, "script", side_effect=RuntimeError("down")),
            caplog.at_level("ERROR", logger="next.suspense"),
        ):
            page = _stream(pages, "/one/")
        assert page.ended is True
        assert page.tail.strip() == "</body></html>"
        assert caplog.text.count("A frame of a stream failed to build") == 1

    @pytest.mark.usefixtures("one_page")
    def test_closing_mid_stream_cancels_the_jobs_and_frees_the_slot(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            capture_signals(page_stream_closed, boundary_rendered) as recorder,
            streaming(),
        ):
            response = NextClient().get("/one/")
            assert slots.open == 1
            next(iter(response.streaming_content))
            assert slots.open == 1
            response.close()
        closed = [e for e in recorder.events if e.signal is page_stream_closed]
        assert [event.kwargs["aborted"] for event in closed] == [True]
        assert not [e for e in recorder.events if e.signal is boundary_rendered]
        assert slots.open == 0

    @pytest.mark.usefixtures("one_page")
    def test_a_stream_failing_in_the_view_frees_its_slot(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            streaming(),
            mock.patch(
                "next.views.pages.finish_response", side_effect=RuntimeError(DOWN)
            ),
            pytest.raises(RuntimeError, match=DOWN),
        ):
            NextClient().get("/one/")
        assert slots.open == 0

    @pytest.mark.usefixtures("one_page")
    def test_a_middleware_failing_after_the_view_holds_no_slot(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            override_next_settings(DEFER={"MAX_STREAMS": 1}),
            streaming(),
        ):
            with override_settings(MIDDLEWARE=[_BOOM, *settings.MIDDLEWARE]):
                client = NextClient(raise_request_exception=False)
                failed = [client.get("/one/").status_code for _ in range(3)]
            del client
            assert slots.open == 0
            response = NextClient().get("/one/")
            response.close()
        assert failed == [500, 500, 500]
        assert isinstance(response, StreamingPageResponse)

    @pytest.mark.usefixtures("one_page")
    def test_past_max_streams_the_page_is_assembled(
        self, pages: Path, slots: StreamSlots
    ) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            override_next_settings(DEFER={"MAX_STREAMS": 1}),
            streaming(),
        ):
            first = NextClient().get("/one/")
            next(iter(first.streaming_content))
            second = NextClient().get("/one/")
            first.close()
        assert isinstance(first, StreamingPageResponse)
        assert not isinstance(second, StreamingPageResponse)
        assert b" yes</p>" in second.content

    def test_a_page_without_a_job_answers_plainly(self, pages: Path) -> None:
        write_page(pages, "plain", _PUBLIC, body="<p>plain</p>")
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS), streaming():
            response = NextClient().get("/plain/")
        assert not isinstance(response, StreamingPageResponse)
        with pytest.raises(AssertionError, match="without a stream"):
            _stream(pages, "/plain/")

    @pytest.mark.usefixtures("one_page")
    def test_the_stream_headers_are_settled(self, pages: Path) -> None:
        response = _stream(pages, "/one/").response
        assert response["Cache-Control"] == "private, no-cache"
        assert response["X-Accel-Buffering"] == "no"
        vary = response["Vary"]
        assert "Accept-Encoding" in vary
        assert "Accept" in vary.split(", ")
        assert "X-Next-Request" in vary
        assert not response.has_header("Content-Length")
        assert not response.has_header("X-Next-Stream")

    @override_settings(DEBUG=True)
    @pytest.mark.usefixtures("one_page")
    def test_head_names_the_shell_delivery(self, pages: Path) -> None:
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS), streaming():
            response = NextClient().head("/one/")
        assert response["X-Next-Stream"] == "shell (head)"
        assert response["X-Next-Boundaries"] == "slow=stream"

    def test_a_page_set_no_store_survives_the_stream(self, pages: Path) -> None:
        write_page(
            pages, "one", "cache = False\n", body="<main>{% component 'slow' %}</main>"
        )
        response = _stream(pages, "/one/").response
        directives = response["Cache-Control"].split(", ")
        assert "no-store" in directives
        assert directives[-2:] == ["private", "no-cache"]

    @pytest.mark.usefixtures("one_page")
    def test_every_inline_tag_carries_the_nonce_of_the_shell(self, pages: Path) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, _NONCE]):
            page = _stream(pages, "/one/")
        written = b"".join(page.writes).decode()
        tags = re.findall(
            r"<(?:script|style)(?=[\t\n\f\r />])[^>]*>", written, re.IGNORECASE
        )
        inline = [tag for tag in tags if "src=" not in tag]
        assert len(inline) >= 4
        assert all(f'nonce="{NONCE_VALUE}"' in tag for tag in inline)

    def test_a_page_opting_out_streams_plain(self, pages: Path) -> None:
        source = "from next.pages import page\n\npage.compress(False)\n"
        write_page(pages, "one", source, body="<main>{% component 'slow' %}</main>")
        page = _stream(pages, "/one/", HTTP_ACCEPT_ENCODING="gzip")
        assert not page.response.has_header("Content-Encoding")
        assert page.ended is True

    @pytest.mark.usefixtures("one_page")
    def test_get_stream_decodes_a_compressed_page(self, pages: Path) -> None:
        page = _stream(pages, "/one/", HTTP_ACCEPT_ENCODING="gzip")
        assert page.response["Content-Encoding"] == "gzip"
        assert page.ended is True
        assert page.chunks[0].html.endswith("yes</p>")
        assert page.tail == "</body></html>"

    @override_settings(DEBUG=True)
    @pytest.mark.usefixtures("one_page")
    def test_debug_names_the_delivery_and_the_boundaries(self, pages: Path) -> None:
        page = _stream(pages, "/one/")
        assert page.response["X-Next-Stream"] == "stream"
        assert page.response["X-Next-Boundaries"] == "slow=stream"
        assert set(page.chunks[0].debug or {}) == {
            "lane",
            "placement",
            "queue_ms",
            "run_ms",
        }
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
            inline = NextClient().get("/one/")
        assert inline["X-Next-Stream"] == "inline (sync-worker)"
        assert inline["X-Next-Boundaries"] == "slow=stream"

    @pytest.mark.usefixtures("one_page")
    def test_the_stream_is_announced_open_and_closed(self, pages: Path) -> None:
        with capture_signals(page_stream_opened, page_stream_closed) as recorder:
            _stream(pages, "/one/")
        opened, closed = recorder.events
        assert opened.kwargs["reason"] == "stream"
        assert opened.kwargs["boundaries"] == (("slow", "stream"),)
        assert opened.kwargs["request_lane"] == "atomic"
        assert closed.kwargs["aborted"] is False
        assert (closed.kwargs["sent"], closed.kwargs["failed"]) == (1, 0)

    @pytest.mark.usefixtures("one_page")
    def test_a_negotiated_stream_decodes_write_by_write(self, pages: Path) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            boundary_gate(component="slow") as gate,
            streaming(),
        ):
            response = NextClient().get("/one/", HTTP_ACCEPT_ENCODING="gzip")
            decoder = zlib.decompressobj(31)
            writes = iter(response.streaming_content)
            shell = decoder.decompress(next(writes))
            gate.release()
            rest = [decoder.decompress(write) for write in writes]
            response.close()
        assert response["Content-Encoding"] == "gzip"
        assert b'data-next-state="loading"' in shell
        assert b"data-next-streamed" in shell
        assert all(rest)
        assert b"yes" in rest[0]
        assert b"push(0)</script></body></html><!--" in rest[-1]
        assert decoder.eof is True

    def test_the_async_client_reads_the_stream(self, pages: Path) -> None:
        write_component(
            pages,
            "broken",
            module=_RAISING.format(error='ValueError("down")'),
            error="<p>broken</p>",
        )
        write_page(
            pages,
            "both",
            _PUBLIC,
            body="<main>{% component 'slow' %}{% component 'broken' %}</main>",
        )
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            boundary_errors("render"),
            capture_signals(boundary_rendered) as recorder,
        ):
            page = asyncio.run(aread_stream(AsyncClient(), "/both/"))
        assert page.response.is_async is True
        assert sorted(chunk.state for chunk in page.chunks) == ["error", "ready"]
        assert page.ended is True
        assert len(recorder.events) == 2
