import threading
from collections.abc import Callable
from unittest.mock import patch

import pytest
from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.template import Context
from django.test import override_settings
from django.utils.functional import SimpleLazyObject

from next.defer.assembly import assembled, page_sink, pages as assembly_pages
from next.defer.assembly.delivery import PageRender, page_deferring
from next.defer.assembly.pages import page_response
from next.defer.markers import Delivery, Job
from next.defer.policy import DeliveryDecision, open_streams
from next.defer.response import StreamingPageResponse
from next.testing import override_next_settings
from tests.support.boundaries import make_call, open_session
from tests.support.waits import WAIT


def _page_response(
    request: HttpRequest, queued: Callable[..., tuple[str, Job]]
) -> HttpResponseBase:
    """Render a page that queues one job and return the response of its view."""
    with page_deferring(request) as rendering:
        page_sink.queue(Context(), make_call())
        html = assembled(lambda: queued()[0], collector=None, request=request)
    return page_response(html, rendering, request)


class TestPageResponse:
    """The view answers plainly without a stream and frees a slot a failure held."""

    def test_a_stream_response_takes_over_the_reservation(self, queued, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        with page_deferring(request) as rendering:
            page_sink.queue(Context(), make_call())
            html = assembled(lambda: queued()[0], collector=None, request=request)
            assert open_streams.open == before + 1
        response = page_response(html, rendering, request)
        assert isinstance(response, StreamingPageResponse)
        assert rendering.slot is None
        assert open_streams.open == before + 1
        response.close()
        assert open_streams.open == before

    def test_a_stream_response_dropped_unread_gives_its_slot_back(
        self, queued, rf
    ) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        response = _page_response(request, queued)
        assert isinstance(response, StreamingPageResponse)
        assert open_streams.open == before + 1
        del response
        assert open_streams.open == before

    def test_a_burst_admits_no_more_streams_than_max_streams(self, queued, rf) -> None:
        before = open_streams.open
        decided = threading.Barrier(8, timeout=WAIT)
        responses: list[HttpResponseBase] = []
        lock = threading.Lock()

        def answer() -> None:
            request = rf.get("/", **{"wsgi.multithread": True})
            response = _page_response(request, queued)
            with lock:
                responses.append(response)
            decided.wait(WAIT)

        with override_next_settings(DEFER={"MAX_STREAMS": before + 4}):
            threads = [threading.Thread(target=answer) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(WAIT)
                assert not thread.is_alive()
        streamed = [
            response
            for response in responses
            if isinstance(response, StreamingPageResponse)
        ]
        assert len(responses) == 8
        assert len(streamed) == 4
        assert open_streams.open == before + 4
        for response in responses:
            response.close()
        assert open_streams.open == before

    @pytest.mark.parametrize(
        ("csp", "minted"), [(["csp.middleware.CSPMiddleware"], ["n0"]), ([], [])]
    )
    def test_a_csp_middleware_mints_the_nonce_before_the_stream_starts(
        self, queued, rf, csp: list[str], minted: list[str]
    ) -> None:
        reads: list[str] = []

        def mint() -> str:
            reads.append("n0")
            return "n0"

        request = rf.get("/", **{"wsgi.multithread": True})
        request.csp_nonce = SimpleLazyObject(mint)
        middleware = [*settings.MIDDLEWARE, *csp]
        with (
            override_settings(MIDDLEWARE=middleware),
            override_next_settings(CSP_NONCE=False),
        ):
            response = _page_response(request, queued)
        assert isinstance(response, StreamingPageResponse)
        response.close()
        assert reads == minted

    def test_a_page_without_a_render_block_is_plain(self, rf) -> None:
        response = page_response("x", None, rf.get("/"))
        assert response.content == b"x"

    def test_a_failed_stream_frees_its_slot(self, rf) -> None:
        rendering = PageRender()
        rendering.session = open_session()
        rendering.delivered = DeliveryDecision(Delivery.STREAM, "stream")
        slot = open_streams.acquire(100)
        assert slot is not None
        rendering.slot = slot
        before = open_streams.open
        with (
            patch.object(assembly_pages, "_stream", side_effect=RuntimeError("down")),
            pytest.raises(RuntimeError, match="down"),
        ):
            page_response("x", rendering, rf.get("/"))
        assert open_streams.open == before - 1
