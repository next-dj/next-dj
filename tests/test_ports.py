from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path

from next.defer.assembly import inline_sink
from next.defer.manager import defer_manager
from next.forms.manager import FormActionManager
from next.forms.ports import ActionOwnersImpl
from next.pages.ports import PageScanImpl
from next.partial.layers import stash_layers
from next.ports import (
    PortList,
    PortSlot,
    action_owners_slot,
    deferrals_slot,
    form_routes_slot,
    layer_loading_slot,
    marked_zones,
    page_scan_slot,
    page_scripts,
    page_views_slot,
    render_deferred,
    route_access_slot,
    router_stamp_slot,
    seo_routes_slot,
    server_backlog,
    server_load_slot,
    stamp_router,
    stash_layer_loading,
    static_assets_slot,
    zone_marking_slot,
)
from next.router.navigation import action_marking, stamp
from next.seo.ports import SeoRoutesImpl
from next.static.ports import StaticAssetsImpl
from next.urls.manager import _LazyUrlPatterns
from next.urls.ports import RouteAccessImpl
from next.views import page_view


PROCESS_SLOTS = [
    pytest.param(action_owners_slot, ActionOwnersImpl, id="action_owners"),
    pytest.param(form_routes_slot, FormActionManager, id="form_routes"),
    pytest.param(page_scan_slot, PageScanImpl, id="page_scan"),
    pytest.param(route_access_slot, RouteAccessImpl, id="route_access"),
    pytest.param(seo_routes_slot, SeoRoutesImpl, id="seo_routes"),
    pytest.param(static_assets_slot, StaticAssetsImpl, id="static_assets"),
]

SLOT_SUBJECTS = [
    "action owners port",
    "deferrals port",
    "form routes port",
    "page scan port",
    "page views port",
    "route access port",
    "seo routes port",
    "server load port",
    "static assets port",
    "zone marking port",
]


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="unbound"):
            PortSlot("route access port").get()

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

    def test_the_deferrals_slot_holds_the_assembled_inline_sink(self) -> None:
        assert deferrals_slot.get() is inline_sink

    def test_the_router_stamp_slot_holds_the_router_stamp(self) -> None:
        assert router_stamp_slot.get() is stamp

    def test_the_zone_marking_slot_holds_the_action_marking(self) -> None:
        assert zone_marking_slot.get() is action_marking

    def test_the_layer_loading_slot_holds_the_layer_stash(self) -> None:
        assert layer_loading_slot.get() is stash_layers

    def test_the_page_views_slot_holds_the_page_view_factory(self) -> None:
        assert page_views_slot.get() is page_view

    def test_the_server_load_slot_holds_the_boundary_pool(self) -> None:
        assert server_load_slot.get() is defer_manager


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


class TestStashLayerLoading:
    """`stash_layer_loading` runs the bound layer loading pass, bound or not."""

    def test_a_bound_pass_receives_the_render(self) -> None:
        slot: PortSlot[object] = PortSlot("layer loading port")
        seen: list[object] = []

        def bound(html, collector, request):
            seen.extend((html, collector, request))
            return "stashed"

        slot.set(bound)
        collector, request = object(), object()
        with patch("next.ports.layer_loading_slot", slot):
            assert stash_layer_loading("<a>", collector, request) == "stashed"
        assert seen == ["<a>", collector, request]

    def test_an_unbound_pass_keeps_the_html(self) -> None:
        slot: PortSlot[object] = PortSlot("layer loading port")
        with patch("next.ports.layer_loading_slot", slot):
            assert stash_layer_loading("<a>", object(), None) == "<a>"


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


class TestServerBacklog:
    """`server_backlog` reads the bound server load, and nothing lags unbound."""

    def test_a_bound_load_reports_its_backlog(self) -> None:
        slot: PortSlot[object] = PortSlot("server load port")
        slot.set(SimpleNamespace(backlog=lambda: 2.5))
        with patch("next.ports.server_load_slot", slot):
            assert server_backlog() == 2.5

    def test_an_unbound_load_reports_no_backlog(self) -> None:
        slot: PortSlot[object] = PortSlot("server load port")
        with patch("next.ports.server_load_slot", slot):
            assert server_backlog() is None


class _Named:
    def __init__(self, name: str, priority: int) -> None:
        self.name = name
        self.priority = priority


class TestPortList:
    """A list holds ranked implementations, one per name, and versions every change."""

    def test_the_rank_orders_and_a_tie_keeps_the_registration_order(self) -> None:
        listed = PortList[_Named]()
        for item in (_Named("b", 2), _Named("a", 1), _Named("c", 2)):
            listed.register(item)
        assert [item.name for item in listed] == ["a", "b", "c"]
        assert listed.version == 3

    def test_a_name_registered_again_replaces_the_first(self) -> None:
        listed = PortList[_Named]()
        first, second = _Named("a", 1), _Named("a", 3)
        listed.register(first)
        listed.register(_Named("b", 2))
        listed.register(second)
        assert [(item.name, item.priority) for item in listed] == [("b", 2), ("a", 3)]

    def test_a_reset_empties_the_list_and_moves_the_version(self) -> None:
        listed = PortList[_Named]()
        listed.register(_Named("a", 1))
        listed.reset()
        assert list(listed) == []
        assert listed.version == 2


class TestPageScripts:
    """The app config registers every built-in page script provider in rank order."""

    def test_the_providers_run_in_rank_order(self) -> None:
        assert [item.name for item in page_scripts] == [
            "release",
            "realtime",
            "stream",
            "router",
            "scripts",
        ]
