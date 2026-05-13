from __future__ import annotations

import pytest

from next.forms.backends import FormActionOptions, RegistryFormActionBackend
from next.forms.signals import action_registered
from tests.benchmarks.factories import noop_form_handler, noop_signal_receiver


_BULK = 100


class TestBenchFormActionBackend:
    @pytest.mark.benchmark(group="forms.backends")
    def test_register_bulk(self, benchmark) -> None:
        def run() -> None:
            backend = RegistryFormActionBackend()
            for i in range(_BULK):
                backend.register_action(
                    f"act_{i}",
                    noop_form_handler,
                    options=FormActionOptions(),
                )

        benchmark(run)

    @pytest.mark.benchmark(group="forms.backends")
    def test_register_bulk_with_receiver(self, benchmark) -> None:
        """Cost of ``action_registered.send`` with one user receiver attached."""
        action_registered.connect(noop_signal_receiver)
        try:

            def run() -> None:
                backend = RegistryFormActionBackend()
                for i in range(_BULK):
                    backend.register_action(
                        f"act_{i}",
                        noop_form_handler,
                        options=FormActionOptions(),
                    )

            benchmark(run)
        finally:
            action_registered.disconnect(noop_signal_receiver)

    @pytest.mark.benchmark(group="forms.backends")
    def test_get_meta_hit(
        self, populated_form_backend: RegistryFormActionBackend, benchmark
    ) -> None:
        benchmark(populated_form_backend.get_meta, f"act_{_BULK // 2}")

    @pytest.mark.benchmark(group="forms.backends")
    def test_get_meta_miss(
        self, populated_form_backend: RegistryFormActionBackend, benchmark
    ) -> None:
        benchmark(populated_form_backend.get_meta, "nonexistent")

    @pytest.mark.benchmark(group="forms.backends")
    def test_generate_urls_with_actions(
        self, populated_form_backend: RegistryFormActionBackend, benchmark
    ) -> None:
        benchmark(populated_form_backend.generate_urls)
