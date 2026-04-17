from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from myapp.apps import MyAppConfig

from next.static import StaticFilesBackend, static_manager


if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.parametrize(
    "url",
    [
        "/static/next/layout.css",
        "/static/next/index.css",
        "/static/next/components/widget.css",
        "/static/next/components/counter.css",
        "/static/next/layout.js",
        "/static/next/index.js",
        "/static/next/components/widget.js",
    ],
)
def test_home_contains_colocated_urls(home_html: str, url: str) -> None:
    assert url in home_html


@pytest.mark.parametrize(
    "url",
    [
        "/static/next/layout.css",
        "/static/next/dashboard.css",
        "/static/next/components/chart.css",
        "/static/next/layout.js",
        "/static/next/components/chart.js",
    ],
)
def test_dashboard_contains_colocated_urls(dashboard_html: str, url: str) -> None:
    assert url in dashboard_html


def test_example_uses_staticfiles_backend() -> None:
    assert isinstance(static_manager.default_backend, StaticFilesBackend)


def test_app_config_metadata() -> None:
    assert MyAppConfig.name == "myapp"


def test_home_and_dashboard_routes_render(client: Client) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/dashboard/").status_code == 200
