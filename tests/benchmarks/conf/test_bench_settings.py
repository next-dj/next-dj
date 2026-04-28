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

    @pytest.mark.benchmark(group="conf.settings")
    def test_merge_with_user_form_action_backends(self, benchmark) -> None:
        """Cost of merging a user-provided ``DEFAULT_FORM_ACTION_BACKENDS`` list.

        Mirrors the existing per-key benches and quantifies the merge
        overhead added by the new top-level setting.
        """
        user_dict = {
            "DEFAULT_FORM_ACTION_BACKENDS": [
                {"BACKEND": "myapp.backends.AuditedBackend", "OPTIONS": {}},
                {"BACKEND": "myapp.backends.MetricsBackend", "OPTIONS": {}},
            ],
        }
        settings = NextFrameworkSettings()
        benchmark(settings._build_flat_merged, user_dict)
