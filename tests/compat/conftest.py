import copy

import pytest
from django.core.cache import cache

from next.forms.autodiscover import _discovered
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics
from next.forms.wizard import wizard_backend_manager
from next.testing import NextClient


@pytest.fixture()
def next_client():
    """Test client without CSRF checks (form action POSTs supply fields manually)."""
    return NextClient(enforce_csrf_checks=False)


@pytest.fixture(autouse=True)
def _isolate_form_registries():
    """Snapshot and restore the form registry around each test.

    Compat modules register forms and actions at import time, so the
    snapshot taken here always includes them. Fixture-registered actions
    are dropped on restore.
    """
    backend = form_action_manager.default_backend
    registry_snapshot = copy.deepcopy(backend._registry)
    uid_snapshot = copy.deepcopy(backend._uid_to_name)
    name_index_snapshot = copy.deepcopy(backend._name_index)
    discovered_snapshot = set(_discovered)
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

    _discovered.clear()
    _discovered.update(discovered_snapshot)

    registration_diagnostics.restore(diagnostics_snapshot)

    wizard_backend_manager.reset()
    cache.clear()
