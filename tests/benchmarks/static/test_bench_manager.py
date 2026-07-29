from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory, override_settings

from next.static.assets import StaticAsset
from next.static.collector import StaticCollector, default_placeholders
from next.static.manager import StaticManager
from next.static.scripts import NextScriptBuilder


if TYPE_CHECKING:
    from typing import Any

    from django.http import HttpRequest


_PAGE_ASSETS = 8
_PAGE_SECTIONS = 30
_SMALL_KEYS = 2
_WIDE_KEYS = 40
_NEXT_JS_URL = "/static/next/next.min.js"


def _page_html(sections: int) -> str:
    tokens = {slot.name: slot.token for slot in default_placeholders}
    body = "".join(f"<section><p>row {i}</p></section>" for i in range(sections))
    return (
        f"<html><head><title>Bench</title>{tokens['styles']}</head>"
        f"<body>{body}{tokens['scripts']}</body></html>"
    )


def _typical_collector() -> StaticCollector:
    collector = StaticCollector()
    for i in range(_PAGE_ASSETS):
        collector.add(StaticAsset(url=f"/static/bench_{i}.css", kind="css"))
        collector.add(StaticAsset(url=f"/static/bench_{i}.js", kind="js"))
    collector.add_js_context("page", {"slug": "bench", "id": 7})
    collector.add_js_context("flags", {"beta": True, "rows": list(range(10))})
    return collector


def _context_pair(count: int) -> tuple[dict[str, Any], dict[str, str]]:
    collector = StaticCollector()
    for i in range(count):
        collector.add_js_context(f"k_{i}", {"n": i, "s": f"v_{i}"})
    return collector.js_context(), collector.js_context_encoded()


def _warmed_manager(
    html: str, collector: StaticCollector, request: HttpRequest | None
) -> StaticManager:
    # The first inject loads the backends and caches the script builder, so the
    # measured calls never pay that one-off setup.
    manager = StaticManager()
    manager.inject(html, collector, request=request)
    return manager


class TestBenchEnsureBackends:
    """`_ensure_backends` guards every discovery and injection entry point."""

    @pytest.mark.benchmark(group="static.manager.load")
    def test_ensure_backends_warm(self, benchmark) -> None:
        """Backends are already loaded, so the call touches no settings."""
        manager = StaticManager()
        manager._ensure_backends()
        benchmark(manager._ensure_backends)

    @pytest.mark.benchmark(group="static.manager.load")
    def test_reload_config_cold(self, benchmark) -> None:
        """A reload rereads settings and rebuilds every configured backend."""
        manager = StaticManager()
        benchmark(manager._reload_config)


class TestBenchStaticManagerInject:
    @pytest.mark.benchmark(group="static.manager")
    def test_inject_typical_page(self, benchmark) -> None:
        html = _page_html(_PAGE_SECTIONS)
        collector = _typical_collector()
        request = RequestFactory().get("/bench/")
        manager = _warmed_manager(html, collector, request)
        benchmark(manager.inject, html, collector, request=request)

    @pytest.mark.benchmark(group="static.manager")
    def test_inject_typical_page_without_request(self, benchmark) -> None:
        html = _page_html(_PAGE_SECTIONS)
        collector = _typical_collector()
        manager = _warmed_manager(html, collector, None)
        benchmark(manager.inject, html, collector, request=None)


class TestBenchReservedPayload:
    # Both cases inject without a request, so minting a CSRF token does not
    # swamp the reserved-key branch this pair is meant to expose.

    @pytest.mark.benchmark(group="static.manager.debug")
    def test_inject_debug_on(self, benchmark) -> None:
        html = _page_html(1)
        collector = _typical_collector()
        with override_settings(DEBUG=True):
            manager = _warmed_manager(html, collector, None)
            benchmark(manager.inject, html, collector, request=None)

    @pytest.mark.benchmark(group="static.manager.debug")
    def test_inject_debug_off(self, benchmark) -> None:
        html = _page_html(1)
        collector = _typical_collector()
        with override_settings(DEBUG=False):
            manager = _warmed_manager(html, collector, None)
            benchmark(manager.inject, html, collector, request=None)


class TestBenchInitScript:
    @pytest.mark.benchmark(group="static.scripts")
    def test_init_script_small_context(self, benchmark) -> None:
        builder = NextScriptBuilder(_NEXT_JS_URL)
        js_context, encoded = _context_pair(_SMALL_KEYS)
        benchmark(builder.init_script, js_context, encoded=encoded)

    @pytest.mark.benchmark(group="static.scripts")
    def test_init_script_wide_context(self, benchmark) -> None:
        builder = NextScriptBuilder(_NEXT_JS_URL)
        js_context, encoded = _context_pair(_WIDE_KEYS)
        benchmark(builder.init_script, js_context, encoded=encoded)

    @pytest.mark.benchmark(group="static.scripts")
    def test_init_script_wide_context_reencoded(self, benchmark) -> None:
        """Every key misses the fragment cache and falls back to the serializer."""
        builder = NextScriptBuilder(_NEXT_JS_URL)
        js_context, _ = _context_pair(_WIDE_KEYS)
        benchmark(builder.init_script, js_context)
