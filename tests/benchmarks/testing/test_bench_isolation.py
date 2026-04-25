"""Benchmarks for ``next.testing.isolation`` reset helpers.

These run between every test in many suites, so a regression here
multiplies across an entire test session.
"""

from __future__ import annotations

import pytest

from next.forms import form_action_manager
from next.forms.backends import FormActionOptions
from next.testing.isolation import reset_form_actions


def _noop_handler(**_: object) -> None:  # pragma: no cover - bench stub
    return None


_REGISTRY_SIZE = 50


class TestBenchResetFormActions:
    @pytest.mark.benchmark(group="testing.isolation")
    def test_reset_form_actions_only(self, benchmark) -> None:
        """Times `reset_form_actions` itself, with a per-round populate setup."""
        form_action_manager._ensure_backends()

        def setup() -> tuple[tuple[object, ...], dict[str, object]]:
            backend = form_action_manager.default_backend
            for i in range(_REGISTRY_SIZE):
                backend.register_action(
                    f"act_{i}",
                    _noop_handler,
                    options=FormActionOptions(),
                )
            return (), {}

        benchmark.pedantic(
            reset_form_actions,
            setup=setup,
            rounds=200,
            iterations=1,
        )
