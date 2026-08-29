import pytest
from django.test import Client

from tests.forms import actions
from tests.support import isolated_form_registries


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
    with isolated_form_registries():
        yield
