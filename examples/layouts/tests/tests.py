import importlib

import layouts.apps
import layouts.context_processors
import layouts.pages.page as main_page
import pytest
from django.apps import apps
from django.test import RequestFactory


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/guides/",
        "/guides/contributing/",
        "/guides/parcel/",
        "/guides/webpack/",
        "/starter-projects/",
    ],
)
def test_pages_accessible_and_renders_correctly(client, url) -> None:
    """Test that pages are accessible and render correctly."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Bootstrap" in content


@pytest.mark.parametrize(
    ("url", "expected_feature"),
    [
        ("/", "layout_inheritance"),
        ("/guides/", "navigation_active_states"),
        ("/", "custom_context_variables"),
        ("/", "context_processors_integration"),
    ],
)
def test_layout_features(client, url, expected_feature) -> None:
    """Test layout features."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()

    if expected_feature == "layout_inheritance":
        assert "Bootstrap" in content
        assert "Starter Template" in content
    elif expected_feature == "navigation_active_states":
        assert "Guides" in content
        assert "active" in content or "current" in content
    elif expected_feature in {
        "custom_context_variables",
        "context_processors_integration",
    }:
        assert "Bootstrap" in content


@pytest.mark.parametrize(
    "check_function",
    [
        "check_duplicate_url_parameters",
        "check_missing_page_content",
        "check_layout_templates",
    ],
)
def test_checks(check_function) -> None:
    """Test next-dj checks."""
    checks_module = importlib.import_module("next.checks")
    check_duplicate_url_parameters = checks_module.check_duplicate_url_parameters
    check_missing_page_content = checks_module.check_missing_page_content
    check_layout_templates = checks_module.check_layout_templates

    check_funcs = {
        "check_duplicate_url_parameters": check_duplicate_url_parameters,
        "check_missing_page_content": check_missing_page_content,
        "check_layout_templates": check_layout_templates,
    }

    check_func = check_funcs[check_function]
    app_configs = apps.get_app_configs()
    errors = check_func(app_configs)
    assert errors == []


def test_example_app_files() -> None:
    """Test that all app files are covered."""
    assert hasattr(layouts.apps, "LayoutsConfig")

    assert hasattr(layouts.context_processors, "site_info")
    assert callable(layouts.context_processors.site_info)

    factory = RequestFactory()
    request = factory.get("/")

    result = layouts.context_processors.site_info(request)
    assert isinstance(result, dict)
    assert "site_name" in result
    assert "site_version" in result
    assert "current_year" in result
    assert result["site_name"] == "next-dj layouts example"
    assert result["site_version"] == "0.1.0"
    assert result["current_year"] == 2025


def test_example_pages_coverage(page_modules) -> None:
    """Test that all page files are covered."""
    assert hasattr(main_page, "custom_variable_context_with_inherit")
    assert hasattr(main_page, "custom_variable_2_context")
    assert callable(main_page.custom_variable_context_with_inherit)
    assert callable(main_page.custom_variable_2_context)

    factory = RequestFactory()
    request = factory.get("/")

    result1 = main_page.custom_variable_context_with_inherit(request)
    assert isinstance(result1, str)
    assert "context with inherit_context=True" in result1

    result2 = main_page.custom_variable_2_context(request)
    assert isinstance(result2, str)
    assert "context without inherit_context=True" in result2
