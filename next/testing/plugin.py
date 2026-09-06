"""Pytest plugin wiring the next.dj test scaffold to ini options and fixtures.

Enabled per project with `-p next.testing.plugin`, which gives a suite the
page loader, the framework client, and cache isolation from its ini file
instead of a conftest copied into every project. Opting in explicitly
rather than through a `pytest11` entry point keeps the framework out of
unrelated pytest runs, and keeps a coverage gate honest, because pytest
imports entry-point plugins before pytest-cov starts measuring.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from .client import NextClient
from .loaders import eager_load_components, eager_load_pages


PAGES_INI = "next_pages"
COMPONENTS_INI = "next_components"
CACHE_INI = "next_clear_cache"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ini options that drive the next.dj fixtures."""
    parser.addini(
        PAGES_INI,
        "Page directories whose `page.py` modules load once per session, "
        "relative to the ini file.",
        type="paths",
        default=[],
    )
    parser.addini(
        COMPONENTS_INI,
        "Import every registered `component.py` once per session.",
        type="bool",
        default=False,
    )
    parser.addini(
        CACHE_INI,
        "Clear the default Django cache before each test.",
        type="bool",
        default=False,
    )


@pytest.fixture(scope="session", autouse=True)
def next_pages(pytestconfig: pytest.Config) -> None:
    """Import the page and component modules named by the ini options.

    Page decorators register as an import side effect, so the modules have
    to run before the first request rather than when a route is missed.
    """
    for directory in pytestconfig.getini(PAGES_INI):
        eager_load_pages(directory)
    if pytestconfig.getini(COMPONENTS_INI):
        eager_load_components()


@pytest.fixture(autouse=True)
def next_cache_isolation(pytestconfig: pytest.Config) -> None:
    """Clear the default cache before each test when `next_clear_cache` is on."""
    if pytestconfig.getini(CACHE_INI):
        cache.clear()


@pytest.fixture()
def next_client() -> NextClient:
    """Return a fresh `NextClient` for one test."""
    return NextClient()


__all__ = ["next_cache_isolation", "next_client", "next_pages", "pytest_addoption"]
