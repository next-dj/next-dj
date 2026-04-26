"""Benchmarks for ``next.forms.backends.FormActionFactory``.

Pins the cost of resolving a backend dotted path through the framework
import cache (warm) versus a fresh import (cold). The factory runs once
per backend entry on first use of the manager.
"""

from __future__ import annotations

import pytest

from next.conf.imports import clear_import_cache
from next.forms.backends import FormActionFactory


_REGISTRY_CONFIG = {"BACKEND": "next.forms.RegistryFormActionBackend"}


class TestBenchFormActionFactory:
    @pytest.mark.benchmark(group="forms.factory")
    def test_create_backend_cached(self, benchmark) -> None:
        """Warm cache: dotted path served from the framework import cache."""
        FormActionFactory.create_backend(_REGISTRY_CONFIG)
        benchmark(FormActionFactory.create_backend, _REGISTRY_CONFIG)

    @pytest.mark.benchmark(group="forms.factory")
    def test_create_backend_cold(self, benchmark) -> None:
        """Cache-miss path: framework import cache cleared per round.

        `clear_import_cache()` only invalidates the per-framework dict
        cache. The underlying module already lives in `sys.modules`, so
        this measures the cache-miss code path, not a full module
        reimport. The bench restores the warm cache on teardown so other
        benches in the same session are not penalised.
        """

        def setup() -> tuple[tuple[object, ...], dict[str, object]]:
            clear_import_cache()
            return (_REGISTRY_CONFIG,), {}

        try:
            benchmark.pedantic(
                FormActionFactory.create_backend,
                setup=setup,
                rounds=200,
                iterations=1,
            )
        finally:
            FormActionFactory.create_backend(_REGISTRY_CONFIG)
