from __future__ import annotations

import pytest

from next.backends import resolve_backend_class
from next.conf.imports import clear_import_cache
from next.forms.backends import FormActionBackend


_REGISTRY_CONFIG = {"BACKEND": "next.forms.RegistryFormActionBackend"}


def _build_backend() -> FormActionBackend:
    """Resolve and instantiate one entry, what `reload` does per config."""
    klass = resolve_backend_class(_REGISTRY_CONFIG, base=FormActionBackend)
    return klass(_REGISTRY_CONFIG)


class TestBenchFormActionBackendLoad:
    @pytest.mark.benchmark(group="forms.backend_load")
    def test_build_backend_cached(self, benchmark) -> None:
        """The dotted path is served from the framework import cache."""
        _build_backend()
        benchmark(_build_backend)

    @pytest.mark.benchmark(group="forms.backend_load")
    def test_build_backend_cold(self, benchmark) -> None:
        """The framework import cache is cleared on every round.

        The module already lives in `sys.modules`, so this measures the cache-miss path,
        and teardown restores the warm cache for the rest of the session.
        """

        def setup() -> None:
            clear_import_cache()

        try:
            benchmark.pedantic(_build_backend, setup=setup, rounds=200, iterations=1)
        finally:
            _build_backend()
