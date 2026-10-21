import pytest
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model

from next.testing import NextClient


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
