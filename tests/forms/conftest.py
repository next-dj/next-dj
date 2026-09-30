import pytest
from django.test import Client

from tests.forms import actions
from tests.support import GuardedTenantForm, isolated_form_registries


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

    Tests that add actions see a clean slate relative to the import-time baseline.
    """
    with isolated_form_registries():
        yield


@pytest.fixture(autouse=True)
def _clear_guarded_tenant_resolutions():
    """Empty the shared provider log on the guarded form around each test.

    The list is class state, so no test reading it may depend on an earlier writer.
    """
    GuardedTenantForm.resolutions.clear()
    yield
    GuardedTenantForm.resolutions.clear()
