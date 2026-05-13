import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

from next.testing import NextClient, eager_load_components, eager_load_pages


EXAMPLE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_ROOT.parent.parent

for path in (EXAMPLE_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if not settings.configured:
    django.setup()

from django.contrib import admin as django_admin  # noqa: E402


django_admin.autodiscover()


@pytest.fixture(autouse=True, scope="session")
def _load_pages() -> None:
    eager_load_pages(EXAMPLE_ROOT / "shadcn_admin" / "surfaces")
    eager_load_components()


@pytest.fixture(autouse=True)
def _isolate(db) -> None:
    cache.clear()


@pytest.fixture()
def client() -> NextClient:
    return NextClient()


@pytest.fixture()
def admin_user(db):
    return get_user_model().objects.create_superuser(
        "admin",
        "admin@example.com",
        "admin-pass",
    )


@pytest.fixture()
def admin_client(admin_user) -> NextClient:
    c = NextClient()
    c.force_login(admin_user)
    return c
