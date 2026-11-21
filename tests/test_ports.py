import threading
from concurrent.futures import Future
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path

from next.forms.manager import FormActionManager
from next.forms.ports import ActionOwnersImpl
from next.pages.ports import PageScanImpl
from next.ports import (
    PortSlot,
    action_owners_slot,
    async_bridge_slot,
    deferrals_slot,
    form_routes_slot,
    marked_zones,
    page_scan_slot,
    page_scripts_slot,
    render_deferred,
    router_access_slot,
    router_stamp_slot,
    run_bridged,
    seo_routes_slot,
    stamp_router,
    static_assets_slot,
    thread_wait_slot,
    wait_served,
    zone_marking_slot,
)
from next.router.navigation import action_marking, stamp
from next.scripts.ports import PageScriptsImpl
from next.seo.ports import SeoRoutesImpl
from next.static.ports import StaticAssetsImpl
from next.suspense import drive, serve_until
from next.suspense.assembly import inline_sink
from next.urls.manager import _LazyUrlPatterns
from next.urls.ports import RouterAccessImpl


PROCESS_SLOTS = [
    pytest.param(action_owners_slot, ActionOwnersImpl, id="action_owners"),
    pytest.param(form_routes_slot, FormActionManager, id="form_routes"),
    pytest.param(page_scan_slot, PageScanImpl, id="page_scan"),
    pytest.param(page_scripts_slot, PageScriptsImpl, id="page_scripts"),
    pytest.param(router_access_slot, RouterAccessImpl, id="router_access"),
    pytest.param(seo_routes_slot, SeoRoutesImpl, id="seo_routes"),
    pytest.param(static_assets_slot, StaticAssetsImpl, id="static_assets"),
]

SLOT_SUBJECTS = [
    "action owners port",
    "async bridge port",
    "deferrals port",
    "form routes port",
    "page scan port",
    "page scripts port",
    "router access port",
    "seo routes port",
    "static assets port",
    "thread wait port",
    "zone marking port",
]


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="unbound"):
            PortSlot("router access port").get()

    @pytest.mark.parametrize("subject", SLOT_SUBJECTS)
    def test_the_message_names_the_subject_the_slot_was_built_with(
        self, subject
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=subject):
            PortSlot(subject).get()

    def test_the_message_names_the_hook_that_binds_the_slot(self) -> None:
        """A read this early means the app never started, so the fix is named."""
        with pytest.raises(ImproperlyConfigured) as caught:
            PortSlot("page scan port").get()

        assert "NextFrameworkConfig.ready()" in str(caught.value)
        assert "never finished starting" in str(caught.value)

    def test_peek_answers_none_before_set(self) -> None:
        """An early reader can tell the app is not ready without catching an error."""
        assert PortSlot("seo routes port").peek() is None


class TestBoundSlot:
    """A bound slot answers the very object it was given."""

    def test_get_returns_the_bound_object(self) -> None:
        slot: PortSlot[object] = PortSlot("page scan port")
        bound = object()
        slot.set(bound)
        assert slot.get() is bound
        assert slot.peek() is bound

    def test_set_replaces_the_previous_binding(self) -> None:
        slot: PortSlot[object] = PortSlot("page scan port")
        slot.set(object())
        replacement = object()
        slot.set(replacement)
        assert slot.get() is replacement


class TestAppComposition:
    """`AppConfig.ready` leaves the process-wide slots bound to the real objects."""

    @pytest.mark.parametrize(("slot", "impl"), PROCESS_SLOTS)
    def test_the_process_slot_holds_its_implementation(self, slot, impl) -> None:
        assert isinstance(slot.get(), impl)

    def test_the_bridge_slot_holds_the_driver(self) -> None:
        assert async_bridge_slot.get() is drive

    def test_the_wait_slot_holds_the_hop_server(self) -> None:
        assert thread_wait_slot.get() is serve_until

    def test_the_deferrals_slot_holds_the_assembled_inline_sink(self) -> None:
        assert deferrals_slot.get() is inline_sink

    def test_the_router_stamp_slot_holds_the_router_stamp(self) -> None:
        assert router_stamp_slot.get() is stamp

    def test_the_zone_marking_slot_holds_the_action_marking(self) -> None:
        assert zone_marking_slot.get() is action_marking


class TestStampRouter:
    """`stamp_router` sets the router directives through the bound stamp, bound or not."""

    def test_a_bound_stamp_receives_the_response(self) -> None:
        slot: PortSlot[object] = PortSlot("router stamp port")
        stamped = HttpResponse()
        seen: list[object] = []

        def bound(response, request):
            seen.extend((response, request))
            return stamped

        slot.set(bound)
        response, request = HttpResponse(), object()
        with patch("next.ports.router_stamp_slot", slot):
            assert stamp_router(response, request) is stamped
        assert seen == [response, request]

    def test_an_unbound_stamp_keeps_the_response(self) -> None:
        slot: PortSlot[object] = PortSlot("router stamp port")
        response = HttpResponse()
        with patch("next.ports.router_stamp_slot", slot):
            assert stamp_router(response, object()) is response


class TestMarkedZones:
    """`marked_zones` opens the block of the bound zone marking, bound or not."""

    def test_a_bound_marking_receives_the_request(self) -> None:
        slot: PortSlot[object] = PortSlot("zone marking port")
        block = nullcontext()
        seen: list[object] = []

        def bound(request):
            seen.append(request)
            return block

        slot.set(bound)
        request = object()
        with patch("next.ports.zone_marking_slot", slot):
            assert marked_zones(request) is block
        assert seen == [request]

    def test_an_unbound_marking_opens_a_plain_block(self) -> None:
        slot: PortSlot[object] = PortSlot("zone marking port")
        with patch("next.ports.zone_marking_slot", slot), marked_zones(object()):
            pass


async def _answer() -> str:
    return "answer"


class TestRunBridged:
    """`run_bridged` runs async work from sync code, bound or not."""

    def test_a_bound_bridge_runs_the_work(self) -> None:
        slot: PortSlot[object] = PortSlot("async bridge port")
        seen: list[object] = []

        def bridge(work):
            seen.append(work)
            return "bridged"

        slot.set(bridge)
        with patch("next.ports.async_bridge_slot", slot):
            assert run_bridged(_answer) == "bridged"
        assert seen == [_answer]

    def test_an_unbound_bridge_falls_back_to_async_to_sync(self) -> None:
        """A custom `AppConfig` that skips composition still runs async work."""
        slot: PortSlot[object] = PortSlot("async bridge port")
        with patch("next.ports.async_bridge_slot", slot):
            assert run_bridged(_answer) == "answer"
            assert run_bridged(_answer()) == "answer"


class TestRenderDeferred:
    """`render_deferred` settles a render through the bound sink, plainly unbound."""

    def test_a_bound_sink_assembles_the_render(self) -> None:
        slot: PortSlot[object] = PortSlot("deferrals port")
        sink = Mock()
        sink.assembled.return_value = "assembled"
        slot.set(sink)
        context: dict[str, object] = {}
        with patch("next.ports.deferrals_slot", slot):
            html = render_deferred(
                _plain, collector=None, request=None, context=context
            )
        assert html == "assembled"
        sink.assembled.assert_called_once_with(
            _plain, collector=None, request=None, context=context
        )

    def test_an_unbound_sink_renders_plainly(self) -> None:
        slot: PortSlot[object] = PortSlot("deferrals port")
        with patch("next.ports.deferrals_slot", slot):
            assert render_deferred(_plain, collector=None, request=None) == "plain"


def _plain() -> str:
    return "plain"


class TestWaitServed:
    """`wait_served` blocks until a future is done, bound or not."""

    def test_a_bound_wait_receives_the_future(self) -> None:
        slot: PortSlot[object] = PortSlot("thread wait port")
        seen: list[object] = []
        slot.set(seen.append)
        future: Future[None] = Future()
        with patch("next.ports.thread_wait_slot", slot):
            wait_served(future)
        assert seen == [future]

    def test_an_unbound_wait_blocks_until_the_future_is_done(self) -> None:
        """A custom `AppConfig` that skips composition still waits."""
        slot: PortSlot[object] = PortSlot("thread wait port")
        future: Future[int] = Future()
        timer = threading.Timer(0.01, future.set_result, (3,))
        timer.start()
        with patch("next.ports.thread_wait_slot", slot):
            wait_served(future)
        timer.join(5)
        assert future.result(0) == 3


class TestUrlsBuiltBeforeTheSeoPortBinds:
    """A URL build that runs before `ready()` binds the SEO port caches nothing stale.

    An app listed ahead of `next` can reverse a URL in `ready()` with the slot unbound.
    """

    def test_the_seo_routes_join_once_the_port_binds(self) -> None:
        sitemap = path("sitemap.xml", lambda _request: HttpResponse(), name="sitemap")
        slot: PortSlot[object] = PortSlot("seo routes port")
        lazy = _LazyUrlPatterns()
        with patch("next.urls.manager.seo_routes_slot", slot):
            early = list(lazy)
            # Bound without bumping the SEO routes version, so an uncached build joins.
            slot.set(SimpleNamespace(patterns=lambda: [sitemap]))
            late = list(lazy)

        assert sitemap not in early
        assert late[-1] is sitemap
