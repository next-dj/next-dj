from __future__ import annotations

import pytest

from next.forms.backends import ActionRegistration
from next.forms.manager import FormActionManager
from next.testing import isolation
from next.testing.isolation import reset_form_actions
from tests.benchmarks.factories import noop_form_handler


_REGISTRY_SIZE = 50


class TestBenchResetFormActions:
    @pytest.mark.benchmark(group="testing.isolation")
    def test_reset_form_actions_only(
        self, benchmark, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Times `reset_form_actions` on a private manager with a per-round populate.

        A reload drops every import-time registration, so the shared manager stays out.
        """
        manager = FormActionManager()
        monkeypatch.setattr(isolation, "form_action_manager", manager)

        def setup() -> tuple[tuple[object, ...], dict[str, object]]:
            backend = manager.default_backend
            for i in range(_REGISTRY_SIZE):
                backend.register_action(
                    ActionRegistration(
                        name=f"act_{i}",
                        file_path=__file__,
                        scope="shared",
                        handler=noop_form_handler,
                    )
                )
            return (), {}

        benchmark.pedantic(reset_form_actions, setup=setup, rounds=200, iterations=1)
