# Canonical scaffold: see docs/content/guide/testing.rst for the rationale.
import os
import sys
from pathlib import Path

import django
import pytest
from django.conf import settings
from django.core.cache import cache

from next.testing import NextClient, eager_load_pages


EXAMPLE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_ROOT.parent.parent

for path in (EXAMPLE_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if not settings.configured:
    django.setup()


@pytest.fixture(autouse=True, scope="session")
def _load_pages() -> None:
    eager_load_pages(EXAMPLE_ROOT / "myapp" / "routes")


@pytest.fixture(autouse=True)
def _isolate() -> None:
    cache.clear()


@pytest.fixture()
def client() -> NextClient:
    return NextClient()
