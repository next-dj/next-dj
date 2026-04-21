"""Benchmarks for ``next.apps.autoreload`` install/uninstall wiring."""

from __future__ import annotations

import pytest

from next.apps.autoreload import install, uninstall


class TestBenchAppsAutoreload:
    @pytest.mark.benchmark(group="apps.autoreload")
    def test_install_uninstall_cycle(self, benchmark) -> None:
        def run() -> None:
            install()
            uninstall()

        benchmark(run)

    @pytest.mark.benchmark(group="apps.autoreload")
    def test_install_idempotent(self, benchmark) -> None:
        install()
        try:
            benchmark(install)
        finally:
            uninstall()
