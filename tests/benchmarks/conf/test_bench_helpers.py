"""Benchmarks for ``next.conf.helpers.extend_default_backend``.

The helper runs only at Django boot time, so these numbers guard against
surprise regressions (e.g. an expensive deep-copy or merge rewrite),
not per-request cost.
"""

from __future__ import annotations

import pytest

from next.conf.helpers import extend_default_backend


class TestBenchExtendDefaultBackend:
    @pytest.mark.benchmark(group="conf.helpers")
    def test_extend_single_override(self, benchmark) -> None:
        benchmark(
            extend_default_backend,
            "DEFAULT_PAGE_BACKENDS",
            PAGES_DIR="routes",
        )

    @pytest.mark.benchmark(group="conf.helpers")
    def test_extend_nested_options_merge(self, benchmark) -> None:
        benchmark(
            extend_default_backend,
            "DEFAULT_PAGE_BACKENDS",
            PAGES_DIR="routes",
            APP_DIRS=True,
            OPTIONS={"context_processors": ["myapp.ctx.one", "myapp.ctx.two"]},
        )
