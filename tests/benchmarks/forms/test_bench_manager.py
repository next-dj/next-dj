"""Benchmarks for ``next.forms.manager.FormActionManager``.

Pins the cost of the lazy ``_ensure_backends`` path, the cold
``_reload_config`` path, and the manager-level ``register_action`` /
``get_action_url`` / ``__iter__`` wrappers. These run on every request
through the form-action subsystem, so any regression here multiplies
across a real workload.
"""

from __future__ import annotations

import pytest

from next.forms import RegistryFormActionBackend
from next.forms.backends import FormActionOptions
from next.forms.manager import FormActionManager
from tests.benchmarks.factories import noop_form_handler


_BULK = 100


def _make_populated_manager() -> FormActionManager:
    manager = FormActionManager()
    manager._ensure_backends()
    backend = manager.default_backend
    for i in range(_BULK):
        backend.register_action(
            f"act_{i}",
            noop_form_handler,
            options=FormActionOptions(),
        )
    return manager


class TestBenchEnsureBackends:
    """`_ensure_backends` is called from every public manager method."""

    @pytest.mark.benchmark(group="forms.manager")
    def test_ensure_backends_warm(self, benchmark) -> None:
        """Hot path: backends already loaded, no settings touch."""
        manager = FormActionManager()
        manager._ensure_backends()
        benchmark(manager._ensure_backends)

    @pytest.mark.benchmark(group="forms.manager")
    def test_reload_config_cold(self, benchmark) -> None:
        """Cold path: drop and reload backends from settings."""
        manager = FormActionManager()

        def run() -> None:
            manager._backends = []
            manager._reload_config()

        benchmark(run)


class TestBenchRegisterThroughManager:
    """`form_action_manager.register_action` adds the wrapper layer over the backend."""

    @pytest.mark.benchmark(group="forms.manager")
    def test_register_bulk_via_manager(self, benchmark) -> None:
        def run() -> None:
            manager = FormActionManager()
            for i in range(_BULK):
                manager.register_action(
                    f"act_{i}",
                    noop_form_handler,
                    options=FormActionOptions(),
                )

        benchmark(run)


class TestBenchManagerLookups:
    """Lookup paths exercised on every URL pattern reload and form render."""

    @pytest.mark.benchmark(group="forms.manager")
    def test_meta_lookup_through_manager(self, benchmark) -> None:
        """Per-action meta lookup walked through the manager iteration."""
        manager = _make_populated_manager()
        target = f"act_{_BULK // 2}"

        def run() -> None:
            for backend in manager._backends:
                if backend.get_meta(target) is not None:
                    return

        benchmark(run)

    @pytest.mark.benchmark(group="forms.manager")
    def test_get_action_url_miss(self, benchmark) -> None:
        """Manager raises ``KeyError`` after walking every backend."""
        manager = _make_populated_manager()

        def run() -> None:
            try:
                manager.get_action_url("nonexistent")
            except KeyError:
                return

        benchmark(run)

    @pytest.mark.benchmark(group="forms.manager")
    def test_default_backend_property(self, benchmark) -> None:
        manager = _make_populated_manager()
        benchmark(lambda: manager.default_backend)

    @pytest.mark.benchmark(group="forms.manager")
    def test_iter_url_patterns(self, benchmark) -> None:
        """`urls/manager.py` materialises this list on every router reload."""
        manager = _make_populated_manager()
        benchmark(lambda: list(manager))


class TestBenchClearRegistries:
    """`clear_registries` runs in test setup; should stay sub-µs per backend."""

    @pytest.mark.benchmark(group="forms.manager")
    def test_clear_registries(self, benchmark) -> None:
        """Times `clear_registries` only, with a per-round re-register setup."""
        manager = _make_populated_manager()
        backend = manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)

        def setup() -> tuple[tuple[object, ...], dict[str, object]]:
            for i in range(_BULK):
                backend.register_action(f"act_{i}", noop_form_handler)
            return (), {}

        benchmark.pedantic(
            manager.clear_registries,
            setup=setup,
            rounds=200,
            iterations=1,
        )
