import copy

import pytest
from django.test import Client

import tests.forms.actions  # noqa: F401 - ensures actions are registered before first snapshot
from next.forms.base import (
    _invalid_meta_scope_classes,
    _outside_base_dir_classes,
    clear_auto_registration_state,
)
from next.forms.checks import _action_collisions, clear_action_collisions
from next.forms.decorators import _action_applied_to_class
from next.forms.manager import form_action_manager


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
    outside_snapshot = list(_outside_base_dir_classes)
    invalid_snapshot = list(_invalid_meta_scope_classes)
    collision_snapshot = copy.deepcopy(_action_collisions)
    class_snapshot = list(_action_applied_to_class)

    yield

    backend._registry.clear()
    backend._registry.update(registry_snapshot)
    backend._uid_to_name.clear()
    backend._uid_to_name.update(uid_snapshot)

    clear_auto_registration_state()
    _outside_base_dir_classes.extend(outside_snapshot)
    _invalid_meta_scope_classes.extend(invalid_snapshot)

    clear_action_collisions()
    _action_collisions.update(collision_snapshot)

    _action_applied_to_class.clear()
    _action_applied_to_class.extend(class_snapshot)
