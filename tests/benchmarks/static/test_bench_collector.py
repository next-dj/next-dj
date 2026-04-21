"""Benchmarks for ``next.static.collector.StaticCollector``."""

from __future__ import annotations

import pytest

from next.static.assets import StaticAsset
from next.static.collector import StaticCollector


_BULK = 100


def _unique_url_assets() -> list[StaticAsset]:
    return [StaticAsset(url=f"/static/c_{i}.css", kind="css") for i in range(_BULK)]


def _inline_assets() -> list[StaticAsset]:
    return [
        StaticAsset(url="", kind="css", inline=f"body{{--v:{i}}}") for i in range(_BULK)
    ]


class TestBenchStaticCollector:
    @pytest.mark.benchmark(group="static.collector")
    def test_add_unique_urls(self, benchmark) -> None:
        assets = _unique_url_assets()

        def run() -> None:
            collector = StaticCollector()
            for asset in assets:
                collector.add(asset)

        benchmark(run)

    @pytest.mark.benchmark(group="static.collector")
    def test_add_dedup_hit(self, benchmark) -> None:
        asset = StaticAsset(url="/static/shared.css", kind="css")

        def run() -> None:
            collector = StaticCollector()
            for _ in range(_BULK):
                collector.add(asset)

        benchmark(run)

    @pytest.mark.benchmark(group="static.collector")
    def test_add_inline_unique(self, benchmark) -> None:
        assets = _inline_assets()

        def run() -> None:
            collector = StaticCollector()
            for asset in assets:
                collector.add(asset)

        benchmark(run)

    @pytest.mark.benchmark(group="static.collector")
    def test_add_js_context_many(self, benchmark) -> None:
        pairs = [(f"k_{i}", {"n": i, "s": f"v_{i}"}) for i in range(_BULK)]

        def run() -> None:
            collector = StaticCollector()
            for key, value in pairs:
                collector.add_js_context(key, value)

        benchmark(run)
