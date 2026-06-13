from __future__ import annotations

import pytest

from next.forms.backends import ActionRegistration, RegistryFormActionBackend
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
                    ActionRegistration(
                        name=f"act_{i}",
                        file_path=__file__,
                        scope="shared",
                        handler=noop_form_handler,
                    )
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
                        ActionRegistration(
                            name=f"act_{i}",
                            file_path=__file__,
                            scope="shared",
                            handler=noop_form_handler,
                        )
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


class TestBenchScopedLookup:
    """Lookups that pass ``page_path`` resolve the path before the keyed hit.

    This is the per-request path the ``{% form %}`` tag drives, distinct from the
    unscoped name-index fallback in ``test_get_meta_hit``.
    """

    @staticmethod
    def _page_scoped_backend() -> RegistryFormActionBackend:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="scoped_action",
                file_path=__file__,
                scope="page",
                handler=noop_form_handler,
            )
        )
        return backend

    @pytest.mark.benchmark(group="forms.backends")
    def test_get_meta_scoped_hit(self, benchmark) -> None:
        backend = self._page_scoped_backend()
        benchmark(backend.get_meta, "scoped_action", __file__)

    @pytest.mark.benchmark(group="forms.backends")
    def test_get_meta_scoped_miss(self, benchmark) -> None:
        backend = self._page_scoped_backend()
        benchmark(backend.get_meta, "scoped_action", "/no/such/page.py")
