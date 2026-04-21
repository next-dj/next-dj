"""Benchmarks for ``next.conf.settings.NextFrameworkSettings``."""

from __future__ import annotations

import pytest

from next.conf.settings import NextFrameworkSettings


class TestBenchSettingsMerge:
    @pytest.mark.benchmark(group="conf.settings")
    def test_merge_cold(self, benchmark) -> None:
        def run() -> None:
            settings = NextFrameworkSettings()
            settings._merged()

        benchmark(run)

    @pytest.mark.benchmark(group="conf.settings")
    def test_merge_warm_cached(self, benchmark) -> None:
        settings = NextFrameworkSettings()
        settings._merged()
        benchmark(settings._merged)

    @pytest.mark.benchmark(group="conf.settings")
    def test_attribute_access_cached(self, benchmark) -> None:
        settings = NextFrameworkSettings()
        _ = settings.DEFAULT_PAGE_BACKENDS
        benchmark(lambda: settings.DEFAULT_PAGE_BACKENDS)

    @pytest.mark.benchmark(group="conf.settings")
    def test_reload_cycle(self, benchmark) -> None:
        settings = NextFrameworkSettings()
        _ = settings.DEFAULT_PAGE_BACKENDS

        def run() -> None:
            settings.reload()
            _ = settings.DEFAULT_PAGE_BACKENDS

        benchmark(run)
