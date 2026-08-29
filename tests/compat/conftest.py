import pytest

from next.testing import NextClient
from tests.support import isolated_form_registries


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
    with isolated_form_registries():
        yield
