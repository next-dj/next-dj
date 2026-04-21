"""Benchmarks for ``next.forms.backends.RegistryFormActionBackend``."""

from __future__ import annotations

import pytest

from next.forms.backends import FormActionOptions, RegistryFormActionBackend


_BULK = 100


def _noop_handler(**_: object) -> None:  # pragma: no cover - just a stub
    return None


class TestBenchFormActionBackend:
    @pytest.mark.benchmark(group="forms.backends")
    def test_register_bulk(self, benchmark) -> None:
        def run() -> None:
            backend = RegistryFormActionBackend()
            for i in range(_BULK):
                backend.register_action(
                    f"act_{i}",
                    _noop_handler,
                    options=FormActionOptions(),
                )

        benchmark(run)

    @pytest.mark.benchmark(group="forms.backends")
    def test_get_meta_hit(self, benchmark) -> None:
        backend = RegistryFormActionBackend()
        for i in range(_BULK):
            backend.register_action(f"act_{i}", _noop_handler)
        benchmark(backend.get_meta, f"act_{_BULK // 2}")

    @pytest.mark.benchmark(group="forms.backends")
    def test_get_meta_miss(self, benchmark) -> None:
        backend = RegistryFormActionBackend()
        for i in range(_BULK):
            backend.register_action(f"act_{i}", _noop_handler)
        benchmark(backend.get_meta, "nonexistent")

    @pytest.mark.benchmark(group="forms.backends")
    def test_generate_urls_with_actions(self, benchmark) -> None:
        backend = RegistryFormActionBackend()
        for i in range(_BULK):
            backend.register_action(f"act_{i}", _noop_handler)
        benchmark(backend.generate_urls)
