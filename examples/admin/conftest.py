import pytest
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model

from next.testing import NextClient


# At import rather than in a fixture, because conftest import precedes the
# plugin's session-scoped page load and the surfaces read the admin registry.
django_admin.autodiscover()


@pytest.fixture()
def admin_user(db):
    return get_user_model().objects.create_superuser(
        "admin", "admin@example.com", "admin-pass"
    )


@pytest.fixture()
def admin_client(admin_user) -> NextClient:
    c = NextClient()
    c.force_login(admin_user)
    return c
