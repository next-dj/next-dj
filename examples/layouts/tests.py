import pytest
from django.apps import apps


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
def test_pages_accessible_and_renders_correctly(client, url):
    """test that pages are accessible and render correctly."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Bootstrap" in content


@pytest.mark.parametrize(
    "url,expected_feature",
    [
        ("/", "layout_inheritance"),
        ("/guides/", "navigation_active_states"),
        ("/", "custom_context_variables"),
        ("/", "context_processors_integration"),
    ],
)
def test_layout_features(client, url, expected_feature):
    """test layout features."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()

    if expected_feature == "layout_inheritance":
        assert "Bootstrap" in content
        assert "Starter Template" in content
    elif expected_feature == "navigation_active_states":
        assert "Guides" in content
        assert "active" in content or "current" in content
    elif expected_feature == "custom_context_variables":
        assert "Bootstrap" in content  # just check that page loads
    elif expected_feature == "context_processors_integration":
        assert "Bootstrap" in content  # just check that page loads


@pytest.mark.parametrize(
    "check_function",
    [
        "check_duplicate_url_parameters",
        "check_missing_page_content",
        "check_layout_templates",
    ],
)
def test_checks(check_function):
    """test next-dj checks."""
    import importlib

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


def test_example_app_files():
    """test that all app files are covered."""
    # test that app files exist and are importable
    import layouts.apps
    import layouts.context_processors

    # test that app config exists
    assert hasattr(layouts.apps, "LayoutsConfig")

    # test that context processor exists
    assert hasattr(layouts.context_processors, "site_info")
    assert callable(layouts.context_processors.site_info)

    # test that context processor can be called
    from django.test import RequestFactory

    factory = RequestFactory()
    request = factory.get("/")

    result = layouts.context_processors.site_info(request)
    assert isinstance(result, dict)
    assert "site_name" in result
    assert "site_version" in result
    assert "current_year" in result
    assert result["site_name"] == "next-dj layouts example"
    assert result["site_version"] == "1.0.0"
    assert result["current_year"] == 2024


def test_example_pages_coverage(page_modules):
    """test that all page files are covered."""
    # test that page files exist and are importable
    import layouts.pages.page as main_page

    # test that context functions exist
    assert hasattr(main_page, "custom_variable_context_with_inherit")
    assert hasattr(main_page, "custom_variable_2_context")
    assert callable(main_page.custom_variable_context_with_inherit)
    assert callable(main_page.custom_variable_2_context)

    # test that context functions can be called with request
    from django.test import RequestFactory

    factory = RequestFactory()
    request = factory.get("/")

    # test context functions
    result1 = main_page.custom_variable_context_with_inherit(request)
    assert isinstance(result1, str)  # returns string, not dict
    assert "context with inherit_context=True" in result1

    result2 = main_page.custom_variable_2_context(request)
    assert isinstance(result2, str)  # returns string, not dict
    assert "context without inherit_context=True" in result2
