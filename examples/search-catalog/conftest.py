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
    """Load every page module under the storefront tree once per session."""
    eager_load_pages(EXAMPLE_ROOT / "catalog" / "storefront")


@pytest.fixture(autouse=True)
def _isolate(db) -> None:
    """Reset the LocMem cache between tests so cache-hit checks are reliable."""
    cache.clear()


@pytest.fixture()
def client() -> NextClient:
    """Return a fresh `NextClient` for each test."""
    return NextClient()


@pytest.fixture()
def catalog_db(db) -> None:
    """Mark a test as depending on the pre-loaded demo catalog."""
    return
