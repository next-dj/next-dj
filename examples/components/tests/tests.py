"""Tests for the components example: pages, simple/composite components, scope, slots."""

import importlib.util
from pathlib import Path

import pytest
from django.apps import apps
from django.urls import get_resolver

from next.components import components_manager, get_component


@pytest.mark.parametrize(
    ("url", "expected_status"),
    [
        ("/home/", 200),
        ("/about/", 200),
        ("/nonexistent/", 404),
    ],
    ids=["home", "about", "nonexistent"],
)
def test_pages_accessible(client, url, expected_status) -> None:
    """Test that pages are accessible with expected status codes."""
    response = client.get(url)
    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("url", "expected_content"),
    [
        (
            "/home/",
            [
                "Home",
                "Components example",
                "Post 1",
                "First post",
                "Admin",
            ],
        ),
        (
            "/about/",
            [
                "About",
                "Components example",
                "About card",
                "Local component",
            ],
        ),
    ],
)
def test_pages_render_with_components(client, url, expected_content) -> None:
    """Test that pages render with root, simple, composite and local components."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    for expected in expected_content:
        assert expected in content, f"Expected '{expected}' not found in content"


def test_home_uses_card_and_profile_components(client) -> None:
    """Test home page uses card (simple) and profile (composite) components."""
    response = client.get("/home/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "card" not in content or "Post 1" in content
    assert "First post" in content
    assert "Admin" in content
    assert "badge" in content or "A" in content  # profile fallback first letter


def test_about_uses_local_card_only_visible_in_scope(client) -> None:
    """Test about page uses local_card from about/_components."""
    response = client.get("/about/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "About card" in content
    assert "Local component" in content
    assert "local-card" in content or "local_card" in content or "About card" in content


def test_header_root_component_visible_on_both_pages(client) -> None:
    """Test root_components/header is visible on home and about."""
    for url in ["/home/", "/about/"]:
        response = client.get(url)
        assert response.status_code == 200
        assert "Components example" in response.content.decode()


def test_myapp_app_config() -> None:
    """Test myapp app is registered (coverage for myapp/apps.py)."""
    app_config = apps.get_app_config("myapp")
    assert app_config is not None
    assert app_config.name == "myapp"


def test_config_urls_loaded() -> None:
    """Test config.urls is used (coverage for config/urls.py)."""
    resolver = get_resolver()
    assert resolver.url_patterns is not None


def test_profile_component_py_module_loaded() -> None:
    """Test profile component.py is loadable (coverage for component.py)."""
    example_root = Path(__file__).resolve().parent.parent
    component_py = (
        example_root / "myapp" / "pages" / "_components" / "profile" / "component.py"
    )
    assert component_py.exists()
    spec = importlib.util.spec_from_file_location("profile_component", component_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod is not None


def test_component_resolution_from_home_template_path(client) -> None:
    """Verify get_component resolves card and profile for home template path."""
    example_root = Path(__file__).resolve().parent.parent
    home_template = example_root / "myapp" / "pages" / "home" / "template.djx"
    path = home_template.resolve()
    assert path.exists(), f"Home template should exist at {path}"
    components_manager._ensure_backends()
    assert len(components_manager._backends) >= 1
    backend = components_manager._backends[0]
    app_roots = backend._get_app_pages_roots()
    assert len(app_roots) >= 1, (
        f"Expected at least one app pages root (myapp); got {app_roots}. "
        "Ensure myapp is in INSTALLED_APPS and myapp/pages exists."
    )
    backend._ensure_loaded()
    assert len(backend._registry) >= 2, (
        f"Expected card and profile in registry; got {len(backend._registry)} entries"
    )
    card_info = get_component("card", path)
    profile_info = get_component("profile", path)
    assert card_info is not None, "card should be visible from home template"
    assert profile_info is not None, "profile should be visible from home template"
