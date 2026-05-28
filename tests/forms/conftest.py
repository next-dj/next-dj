import copy

import pytest
from django.test import Client

from next.forms.autodiscover import _discovered
from next.forms.backends import _action_collisions, clear_action_collisions
from next.forms.base import (
    _instance_from_url_on_non_model_form,
    _instance_from_url_unknown_field,
    _invalid_meta_scope_classes,
    _outside_base_dir_classes,
    clear_auto_registration_state,
)
from next.forms.decorators import _action_applied_to_class
from next.forms.manager import form_action_manager
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
    discovered_snapshot = set(_discovered)
    outside_snapshot = list(_outside_base_dir_classes)
    invalid_snapshot = list(_invalid_meta_scope_classes)
    unknown_field_snapshot = list(_instance_from_url_unknown_field)
    non_model_form_snapshot = list(_instance_from_url_on_non_model_form)
    collision_snapshot = copy.deepcopy(_action_collisions)
    class_snapshot = list(_action_applied_to_class)

    yield

    backend._registry.clear()
    backend._registry.update(registry_snapshot)
    backend._uid_to_name.clear()
    backend._uid_to_name.update(uid_snapshot)
    backend._name_index.clear()
    backend._name_index.update(name_index_snapshot)

    _discovered.clear()
    _discovered.update(discovered_snapshot)

    clear_auto_registration_state()
    _outside_base_dir_classes.extend(outside_snapshot)
    _invalid_meta_scope_classes.extend(invalid_snapshot)
    _instance_from_url_unknown_field.extend(unknown_field_snapshot)
    _instance_from_url_on_non_model_form.extend(non_model_form_snapshot)

    clear_action_collisions()
    _action_collisions.update(collision_snapshot)

    _action_applied_to_class.clear()
    _action_applied_to_class.extend(class_snapshot)
