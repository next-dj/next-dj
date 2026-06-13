from __future__ import annotations

import pytest

from next.forms import Form
from next.forms.manager import form_action_manager


@pytest.fixture()
def _restore_action_registry():
    """Snapshot and restore the default backend registry around the bench."""
    backend = form_action_manager.default_backend
    registry = dict(backend._registry)
    uid_to_name = dict(backend._uid_to_name)
    name_index = dict(backend._name_index)
    url_cache = dict(backend._url_cache)
    yield
    backend._registry.clear()
    backend._registry.update(registry)
    backend._uid_to_name.clear()
    backend._uid_to_name.update(uid_to_name)
    backend._name_index.clear()
    backend._name_index.update(name_index)
    backend._url_cache.clear()
    backend._url_cache.update(url_cache)


class TestBenchFormRegistration:
    """Import-time cost of declaring auto-registered Form subclasses.

    Each declaration runs `__init_subclass__`, the registration gate,
    and the `_find_definition_frame` stack walk. Class names repeat on
    every round so the registry overwrites in place and stays
    size-stable across rounds.
    """

    @pytest.mark.benchmark(group="forms.registration")
    @pytest.mark.usefixtures("_restore_action_registry")
    def test_define_form_subclasses(self, benchmark) -> None:
        metaclass = type(Form)

        def run() -> None:
            for i in range(20):
                metaclass(f"BenchDeclared{i}", (Form,), {})

        benchmark(run)
