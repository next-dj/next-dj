import pytest
from django.test import Client


@pytest.fixture()
def client_no_csrf():
    """Test client without CSRF checks (form action POSTs supply fields manually)."""
    return Client(enforce_csrf_checks=False)
