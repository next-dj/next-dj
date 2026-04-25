"""Benchmarks for ``next.forms.backends.RegistryFormActionBackend``."""

from __future__ import annotations

import pytest

from next.forms.backends import FormActionOptions, RegistryFormActionBackend
from next.forms.signals import action_registered


_BULK = 100


def _noop_handler(**_: object) -> None:  # pragma: no cover - just a stub
    return None


def _noop_receiver(sender: object, **_: object) -> None:  # pragma: no cover
    """No-op listener attached to ``action_registered`` for the bench."""
    del sender


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
    def test_register_bulk_with_receiver(self, benchmark) -> None:
        """Cost of ``action_registered.send`` with one user receiver attached."""
        action_registered.connect(_noop_receiver)
        try:

            def run() -> None:
                backend = RegistryFormActionBackend()
                for i in range(_BULK):
                    backend.register_action(
                        f"act_{i}",
                        _noop_handler,
                        options=FormActionOptions(),
                    )

            benchmark(run)
        finally:
            action_registered.disconnect(_noop_receiver)

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
