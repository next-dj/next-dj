from collections.abc import Callable
from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.template import Context
from django.test import RequestFactory

from next.defer import Delivery
from next.partial.envelope import Envelope
from next.partial.headers import STREAM_ACCEPT
from next.partial.stream import streamable
from next.suspense.assembly import page_sink
from next.suspense.assembly.delivery import page_deferring
from next.suspense.assembly.partials import PartialRender, streamed
from next.suspense.components import deferring
from next.suspense.markers import Job
from next.suspense.partial import PartialStream
from next.suspense.policy import Delivered, StreamSlots, open_streams
from next.suspense.render import Delta
from next.suspense.signals import page_stream_declined
from next.testing import capture_signals
from tests.support.boundaries import make_call


_FAILED = "render failed"


def _rendering(
    rf: RequestFactory, queued: Callable[..., tuple[str, Job[Delta]]]
) -> PartialRender:
    """Return a streamed partial render holding a slot and a shelved job."""
    rendering = PartialRender(rf.get("/"), Delivered(Delivery.STREAM, "stream"))
    slot = open_streams.acquire(100)
    assert slot is not None
    rendering.hold(slot)
    with deferring() as held:
        html = queued()[0]
    assert held.session is not None
    rendering.shelve(held.session, html, None)
    return rendering


class TestPartialRender:
    """A streamed partial render hands its slot to the stream it answers with."""

    def test_the_stream_owns_the_slot_until_it_closes(self, queued, rf) -> None:
        before = open_streams.open
        rendering = _rendering(rf, queued)
        response = rendering.respond(Envelope("v"))
        assert isinstance(response, PartialStream)
        assert rendering.slot is None
        assert open_streams.open == before + 1
        response.close()
        response.close()
        assert open_streams.open == before

    def test_a_stream_that_fails_to_build_frees_the_slot(self, queued, rf) -> None:
        before = open_streams.open
        rendering = _rendering(rf, queued)
        with (
            patch.object(PartialStream, "_coding", side_effect=RuntimeError(_FAILED)),
            pytest.raises(RuntimeError, match=_FAILED),
        ):
            rendering.respond(Envelope("v"))
        assert open_streams.open == before


@pytest.fixture()
def partial_request(rf: RequestFactory) -> Callable[..., HttpRequest]:
    """Build a partial request the way a client of the stream sends it."""

    def build(*, threaded: bool) -> HttpRequest:
        return rf.get(
            "/",
            headers={"X-Next-Request": "1", "Accept": STREAM_ACCEPT},
            **{"wsgi.multithread": threaded},
        )

    return build


def _opened_by(
    request: HttpRequest, queued: Callable[..., tuple[str, Job[Delta]]]
) -> str | None:
    """Return what a partial render keeps for the stream, `None` when it declines."""
    with deferring() as held:
        html = queued()[0]
    assert held.session is not None
    with streamable(request):
        return streamed(request, html, None, held.session)


class TestDeclined:
    """A response whose jobs are assembled inline is announced once, a stream never."""

    def _declined(self, request: HttpRequest) -> list[tuple[object, object]]:
        with (
            capture_signals(page_stream_declined) as recorder,
            page_deferring(request) as rendering,
        ):
            page_sink.queue(Context(), make_call())
            page_sink.queue(Context(), make_call())
        rendering.release()
        return [(event.kwargs["reason"], event.kwargs["partial"]) for event in recorder]

    def test_a_page_on_a_sync_worker_is_announced_once(self, rf) -> None:
        assert self._declined(rf.get("/")) == [("sync-worker", False)]

    def test_a_page_past_max_streams_is_announced(self, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        with patch.object(StreamSlots, "acquire", return_value=None):
            assert self._declined(request) == [("capacity", False)]

    def test_a_streamed_page_is_not_announced(self, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        assert self._declined(request) == []

    def test_a_partial_kept_inline_by_the_policy_is_announced(
        self, queued, partial_request
    ) -> None:
        with capture_signals(page_stream_declined) as recorder:
            assert _opened_by(partial_request(threaded=False), queued) is None
        [event] = recorder
        assert (event.kwargs["reason"], event.kwargs["partial"]) == (
            "sync-worker",
            True,
        )

    def test_a_partial_past_max_streams_is_announced(
        self, queued, partial_request
    ) -> None:
        with (
            capture_signals(page_stream_declined) as recorder,
            patch.object(StreamSlots, "acquire", return_value=None),
        ):
            assert _opened_by(partial_request(threaded=True), queued) is None
        assert [event.kwargs["reason"] for event in recorder] == ["capacity"]

    def test_a_partial_head_answers_no_stream_and_no_announcement(
        self, queued, rf
    ) -> None:
        request = rf.head(
            "/", headers={"X-Next-Request": "1"}, **{"wsgi.multithread": True}
        )
        with capture_signals(page_stream_declined) as recorder:
            assert _opened_by(request, queued) is None
        assert list(recorder) == []
