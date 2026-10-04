from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path

from next.pages.ports import PageScanImpl
from next.partial.ports import PartialShaperImpl
from next.ports import (
    PortSlot,
    page_scan_slot,
    page_scripts_slot,
    partial_shaper_slot,
    router_access_slot,
    seo_routes_slot,
    static_assets_slot,
)
from next.scripts.ports import PageScriptsImpl
from next.seo.ports import SeoRoutesImpl
from next.static.ports import StaticAssetsImpl
from next.urls.manager import _LazyUrlPatterns
from next.urls.ports import RouterAccessImpl
from tests.support import IntentOnlyShaper


PROCESS_SLOTS = [
    pytest.param(page_scan_slot, PageScanImpl, id="page_scan"),
    pytest.param(page_scripts_slot, PageScriptsImpl, id="page_scripts"),
    pytest.param(partial_shaper_slot, PartialShaperImpl, id="partial_shaper"),
    pytest.param(router_access_slot, RouterAccessImpl, id="router_access"),
    pytest.param(seo_routes_slot, SeoRoutesImpl, id="seo_routes"),
    pytest.param(static_assets_slot, StaticAssetsImpl, id="static_assets"),
]

SLOT_SUBJECTS = [
    "page scan port",
    "page scripts port",
    "partial shaper",
    "router access port",
    "seo routes port",
    "static assets port",
]


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="unbound"):
            PortSlot("partial shaper").get()

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
        slot = PortSlot("partial shaper")
        shaper = IntentOnlyShaper()
        slot.set(shaper)
        assert slot.get() is shaper
        assert slot.peek() is shaper

    def test_set_replaces_the_previous_binding(self) -> None:
        slot = PortSlot("partial shaper")
        slot.set(IntentOnlyShaper())
        replacement = IntentOnlyShaper()
        slot.set(replacement)
        assert slot.get() is replacement


class TestAppComposition:
    """`AppConfig.ready` leaves the process-wide slots bound to the real objects."""

    @pytest.mark.parametrize(("slot", "impl"), PROCESS_SLOTS)
    def test_the_process_slot_holds_its_implementation(self, slot, impl) -> None:
        assert isinstance(slot.get(), impl)


class TestUrlsBuiltBeforeTheSeoPortBinds:
    """A URL build that runs before `ready()` binds the SEO port caches nothing stale.

    A project app listed ahead of `next` can reverse a URL in its own `ready()`, so the
    page patterns are built while the slot is unbound.
    """

    def test_the_seo_routes_join_once_the_port_binds(self) -> None:
        sitemap = path("sitemap.xml", lambda _request: HttpResponse(), name="sitemap")
        slot: PortSlot[object] = PortSlot("seo routes port")
        lazy = _LazyUrlPatterns()
        with patch("next.urls.manager.seo_routes_slot", slot):
            early = list(lazy)
            # Bound without moving the SEO routes version, so only an uncached early
            # build lets the routes join.
            slot.set(SimpleNamespace(patterns=lambda: [sitemap]))
            late = list(lazy)

        assert sitemap not in early
        assert late[-1] is sitemap
