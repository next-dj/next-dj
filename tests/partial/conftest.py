import copy

import pytest
from django.core.cache import cache

from next.forms.diagnostics import registration_diagnostics
from next.forms.manager import form_action_manager
from next.forms.wizard import wizard_backend_manager
from tests.partial import regression_forms


# Bind the module so its module-level form action is registered before the
# registry snapshot below, whatever the collection order happens to be.
_BASELINE_FORMS = regression_forms


@pytest.fixture(autouse=True)
def _isolate_form_registries():
    """Snapshot and restore the form registry around each partial test."""
    backend = form_action_manager.default_backend
    registry_snapshot = copy.deepcopy(backend._registry)
    uid_snapshot = copy.deepcopy(backend._uid_to_name)
    name_index_snapshot = copy.deepcopy(backend._name_index)
    diagnostics_snapshot = registration_diagnostics.snapshot()

    wizard_backend_manager.reset()
    cache.clear()

    yield

    backend._registry.clear()
    backend._registry.update(registry_snapshot)
    backend._uid_to_name.clear()
    backend._uid_to_name.update(uid_snapshot)
    backend._name_index.clear()
    backend._name_index.update(name_index_snapshot)

    registration_diagnostics.restore(diagnostics_snapshot)

    wizard_backend_manager.reset()
    cache.clear()
