"""Shared test configuration for the static assets example.

The example ships its own ``config/settings.py``, so the conftest wires
``DJANGO_SETTINGS_MODULE`` to that module and exposes a small set of reusable
fixtures for the test modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


_example_root = Path(__file__).resolve().parent.parent
_repo_root = _example_root.parent.parent

if str(_example_root) not in sys.path:
    sys.path.insert(0, str(_example_root))
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
import pytest  # noqa: E402
from django.apps import apps  # noqa: E402
from django.test import Client  # noqa: E402


if not apps.ready:
    django.setup()


@pytest.fixture()
def client() -> Client:
    """Return a fresh Django test client for HTTP interactions."""
    return Client()


@pytest.fixture()
def home_html(client: Client) -> str:
    """Return the rendered HTML of the home page (``/``)."""
    response = client.get("/")
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture()
def dashboard_html(client: Client) -> str:
    """Return the rendered HTML of the dashboard page (``/dashboard/``)."""
    response = client.get("/dashboard/")
    assert response.status_code == 200
    return response.content.decode()
