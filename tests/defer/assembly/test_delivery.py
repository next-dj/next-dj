import gc
from unittest.mock import Mock, patch

import pytest
from django.template import Context

from next.boundaries.calls import DEFERRALS_KEY
from next.boundaries.scope import render_boundaries
from next.defer.assembly import (
    InlineDeferrals,
    PageDeferrals,
    assembled,
    inline_sink,
    page_sink,
)
from next.defer.assembly.delivery import PageRender, page_deferring
from next.defer.assembly.pages import page_response
from next.defer.components import current_deferring, deferring
from next.defer.connections import forcing_pool
from next.defer.markers import Delivery
from next.defer.policy import DeliveryDecision, StreamSlots, open_streams
from next.defer.signals import boundary_settled
from next.partial.headers import STREAM_ACCEPT
from next.partial.stream import streamable
from next.testing import capture_signals
from tests.support.boundaries import make_call, open_session


_FAILED = "render failed"


class TestAssembled:
    """A render settles every job it queued, unless it shows the shell alone."""

    def test_a_queued_job_is_run_and_spliced(self, queued) -> None:
        html = assembled(lambda: f"[{queued()[0]}]", collector=None, request=None)
        assert html.startswith("[<div id=")
        assert html.endswith('data-next-state="ready">R</div>]')

    def test_an_open_block_takes_the_render_and_seeds_its_sink(self, queued) -> None:
        sink = Mock()
        context: dict[str, object] = {}
        with deferring(sink) as held:
            html = assembled(
                lambda: queued()[0], collector=None, request=None, context=context
            )
        assert context == {DEFERRALS_KEY: sink}
        assert 'data-next-state="ready"' in html
        assert held.session is None

    def test_a_block_without_a_sink_leaves_the_context_alone(self) -> None:
        context: dict[str, object] = {}
        with deferring():
            assembled(lambda: "x", collector=None, request=None, context=context)
        assert context == {}

    def test_a_shell_delivery_runs_no_job_and_drops_its_markers(self, queued) -> None:
        with deferring() as held:
            held.delivered = DeliveryDecision(Delivery.SHELL, "head")
            html = assembled(
                lambda: "<!--b-0-1:div-->" + queued()[0], collector=None, request=None
            )
        assert 'data-next-state="loading"' in html
        assert html.startswith('<!--b-0-1:div--><div id="b-')
        assert html.endswith("</div>")
        assert held.session is None

    def test_a_prefetch_delivery_runs_no_job_either(self, queued) -> None:
        with deferring() as held:
            held.delivered = DeliveryDecision(Delivery.PREFETCH, "prefetch")
            html = assembled(lambda: queued()[0], collector=None, request=None)
        assert 'data-next-state="loading"' in html
        assert held.session is None

    def test_a_partial_shell_that_lost_its_jobs_asks_for_no_stream(
        self, queued, rf
    ) -> None:
        request = rf.get("/", HTTP_ACCEPT=STREAM_ACCEPT)

        def render() -> str:
            queued()
            return "lost"

        with streamable(request) as block:
            html = assembled(render, collector=None, request=request)
        assert html == "lost"
        assert block is not None
        assert (block.asked, block.deferred) == (False, None)

    def test_inside_a_job_the_render_runs_as_it_is(self, in_job) -> None:
        session = open_session()
        in_job(session)
        html = assembled(lambda: "plain", collector=None, request=None)
        assert html == "plain"

    def test_the_inline_sink_assembles_through_the_same_path(self, queued) -> None:
        assert isinstance(inline_sink, InlineDeferrals)
        html = inline_sink.assembled(lambda: queued()[0], collector=None, request=None)
        assert 'data-next-state="ready">R</div>' in html

    def test_a_forced_pool_places_every_job_on_the_pool(self, queued) -> None:
        with forcing_pool(), capture_signals(boundary_settled) as recorder:
            assembled(lambda: queued()[0] + queued()[0], collector=None, request=None)
        reasons = {str(event.kwargs["reason"]) for event in recorder}
        assert reasons == {"pool"}


class TestPageDeferrals:
    """The page sink asks the delivery policy once, at its first queued call."""

    def test_the_first_call_decides_the_delivery(self, rf) -> None:
        request = rf.head("/")
        with deferring(page_sink, request) as held:
            page_sink.queue(Context(), make_call())
            page_sink.queue(Context(), make_call())
        assert held.delivered == DeliveryDecision(Delivery.SHELL, "head")

    def test_a_decided_delivery_stands(self, rf) -> None:
        decided = DeliveryDecision(Delivery.INLINE, "method")
        with deferring(page_sink, rf.head("/")) as held:
            held.delivered = decided
            page_sink.queue(Context(), make_call())
        assert held.delivered is decided

    def test_without_a_request_no_policy_runs(self) -> None:
        with deferring(page_sink) as held:
            page_sink.queue(Context(), make_call())
        assert held.delivered is None

    def test_outside_a_block_the_call_renders_in_place(self) -> None:
        call = make_call()
        call.renderer.body.return_value = ("B", None)
        assert isinstance(page_sink, PageDeferrals)
        assert page_sink.queue(Context(), call).endswith(">B</div>")

    def test_inside_a_job_no_policy_runs(self, in_job, rf) -> None:
        session = open_session()
        in_job(session)
        with deferring(page_sink, rf.get("/")) as held:
            page_sink.queue(Context(), make_call())
        assert held.delivered is None


class TestPageRender:
    """A page render holds its stream slot until a stream response takes it."""

    def test_a_release_gives_the_slot_back_once(self) -> None:
        rendering = PageRender()
        slot = open_streams.acquire(100)
        assert slot is not None
        before = open_streams.open
        rendering.slot = slot
        rendering.release()
        rendering.release()
        assert open_streams.open == before - 1

    def test_a_dropped_render_gives_its_slot_back(self) -> None:
        rendering = PageRender()
        rendering.slot = open_streams.acquire(100)
        assert rendering.slot is not None
        before = open_streams.open
        del rendering
        gc.collect()
        assert open_streams.open == before - 1

    def test_a_failed_render_releases_its_slot(self, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        taken: list[int] = []

        def render() -> None:
            with page_deferring(request):
                page_sink.queue(Context(), make_call())
                taken.append(open_streams.open)
                raise RuntimeError(_FAILED)

        with pytest.raises(RuntimeError, match=_FAILED):
            render()
        assert taken == [before + 1]
        assert open_streams.open == before

    def test_a_lost_race_for_a_slot_assembles_inline(self, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        with (
            patch.object(StreamSlots, "acquire", return_value=None),
            page_deferring(request) as rendering,
        ):
            page_sink.queue(Context(), make_call())
            held = current_deferring()
        assert held is not None
        assert held.delivered == DeliveryDecision(Delivery.INLINE, "capacity")
        assert rendering.slot is None

    def test_without_a_page_render_a_stream_takes_no_slot(self, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        call = make_call()
        call.renderer.body.return_value = ("B", None)
        with deferring(page_sink, request) as held:
            html = assembled(
                lambda: page_sink.queue(Context(), call),
                collector=None,
                request=request,
            )
        assert held.delivered == DeliveryDecision(Delivery.STREAM, "stream")
        assert 'data-next-state="ready"' in html
        assert open_streams.open == before

    def test_a_second_render_of_one_page_assembles_inline(self, queued, rf) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        with page_deferring(request) as rendering:
            page_sink.queue(Context(), make_call())
            first = assembled(lambda: queued()[0], collector=None, request=request)
            second = assembled(lambda: queued()[0], collector=None, request=request)
        rendering.release()
        assert 'data-next-state="loading"' in first
        assert 'data-next-state="ready"' in second

    def test_a_stream_whose_wrappers_were_dropped_assembles_inline(
        self, queued, rf
    ) -> None:
        request = rf.get("/", **{"wsgi.multithread": True})
        with page_deferring(request) as rendering:
            page_sink.queue(Context(), make_call())
            html = assembled(
                lambda: "gone" if queued() else "", collector=None, request=request
            )
        assert html == "gone"
        assert rendering.session is None
        assert page_response(html, rendering, request).content == b"gone"
        assert rendering.slot is None


class TestSharedBoundaryState:
    """One gathering render shares one boundary state, so its wrappers probe once."""

    def test_the_boundaries_of_a_render_share_their_state(self) -> None:
        seen: list[object] = []

        def render() -> str:
            seen.extend(render_boundaries() for _ in range(2))
            return "x"

        assembled(render, collector=None, request=None)
        with deferring():
            assembled(render, collector=None, request=None)
        assert len(seen) == 4
        assert all(scope is seen[0] for scope in seen)
        assert render_boundaries() is not seen[0]
