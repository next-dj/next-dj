import copy

import pytest
from django.core.cache import cache
from django.test import Client

from next.forms.diagnostics import registration_diagnostics
from next.forms.manager import form_action_manager
from next.forms.wizard import wizard_backend_manager
from tests.forms import actions


# `actions` registers baseline form actions on import. Bind it so the registry
# snapshot below always reflects them, whatever the collection order happens to be.
_BASELINE_ACTIONS = actions


@pytest.fixture()
def client_no_csrf():
    """Test client without CSRF checks (form action POSTs supply fields manually)."""
    return Client(enforce_csrf_checks=False)


@pytest.fixture(autouse=True)
def _isolate_form_registries():
    """Snapshot and restore the form registry around each test.

    Tests that add new actions see a clean slate relative to the import-time baseline.
    The baseline is always restored for the next test.
    """
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
