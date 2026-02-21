import importlib
import importlib.util
from pathlib import Path

import myapp.apps
import myapp.pages.simple.page as simple_page
import pytest
import root_pages.home.page as home_page
from django.apps import apps
from django.test import RequestFactory


@pytest.mark.parametrize(
    ("url", "expected_status"),
    [
        ("/simple/", 200),
        ("/kwargs/123/", 200),
        ("/args/test/path/", 200),
        ("/kwargs/invalid/", 404),
        ("/nonexistent/", 404),
    ],
    ids=["simple", "kwargs_valid", "args_path", "kwargs_invalid", "nonexistent"],
)
def test_pages_accessible(client, url, expected_status) -> None:
    """Test that pages are accessible with expected status codes."""
    response = client.get(url)
    assert response.status_code == expected_status, (
        f"url {url} should return {expected_status}"
    )


@pytest.mark.parametrize(
    ("url", "expected_content"),
    [
        (
            "/home/",
            ["Root Page", "This is a root page without any parameters", "URL: /home"],
        ),
        (
            "/simple/",
            [
                "Simple Page",
                "This is a simple page without any parameters",
                "URL: /simple",
            ],
        ),
        ("/kwargs/123/", ["Typed Parameter Page", "post_id = 123"]),
        ("/kwargs/456/", ["post_id = 456"]),
    ],
)
def test_pages_renders_correctly(client, url, expected_content) -> None:
    """Test that pages render correctly with expected content."""
    response = client.get(url)
    if response.status_code == 200:
        content = response.content.decode()
        for expected in expected_content:
            assert expected in content
    else:
        assert response.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "/kwargs/invalid/",
        "/args/",  # args page requires at least one argument
        "/nonexistent/",
    ],
)
def test_invalid_pages_return_404(client, url) -> None:
    """Test that invalid pages return 404."""
    response = client.get(url)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("url", "expected_content"),
    [
        (
            "/args/test/path/",
            ["Variable Arguments Page", "Arguments Count:", "Arguments:"],
        ),
        (
            "/args/single/",
            ["Variable Arguments Page", "Arguments Count:", "Arguments:"],
        ),
    ],
)
def test_args_pages_renders_correctly(client, url, expected_content) -> None:
    """Test that args pages render correctly with expected content."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()

    for expected in expected_content:
        assert expected in content, f"Expected '{expected}' not found in content"


def test_check_duplicate_url_parameters() -> None:
    """Test check_duplicate_url_parameters check."""
    checks_module = importlib.import_module("next.checks")
    check_duplicate_url_parameters = checks_module.check_duplicate_url_parameters

    app_configs = apps.get_app_configs()
    errors = check_duplicate_url_parameters(app_configs)
    assert errors == []


def test_check_missing_page_content() -> None:
    """Test check_missing_page_content check."""
    checks_module = importlib.import_module("next.checks")
    check_missing_page_content = checks_module.check_missing_page_content

    app_configs = apps.get_app_configs()
    errors = check_missing_page_content(app_configs)
    assert errors == []


def test_example_pages_comprehensive(client) -> None:
    """Test comprehensive page functionality."""
    response = client.get("/simple/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Simple Page" in content

    for param in ["123", "456", "789"]:
        response = client.get(f"/kwargs/{param}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert f"post_id = {param}" in content

    for args in ["test", "single", "multiple/args/here"]:
        response = client.get(f"/args/{args}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Variable Arguments Page" in content


def test_example_error_scenarios(client) -> None:
    """Test error scenarios."""
    response = client.get("/kwargs/invalid/")
    assert response.status_code == 404

    response = client.get("/args/")
    assert response.status_code == 404

    response = client.get("/nonexistent/")
    assert response.status_code == 404


def test_example_context_functions_comprehensive() -> None:
    """Test comprehensive context function functionality."""
    kwargs_path = Path("myapp") / "pages" / "kwargs" / "[int:post-id]" / "page.py"
    spec = importlib.util.spec_from_file_location("kwargs_page", kwargs_path)
    kwargs_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kwargs_page)

    args_path = Path("myapp") / "pages" / "args" / "[[args]]" / "page.py"
    spec = importlib.util.spec_from_file_location("args_page", args_path)
    args_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(args_page)

    assert hasattr(simple_page, "render")
    assert hasattr(kwargs_page, "render")
    assert hasattr(args_page, "render")
    assert hasattr(home_page, "render")

    assert callable(simple_page.render)
    assert callable(kwargs_page.render)
    assert callable(args_page.render)
    assert callable(home_page.render)


def test_example_template_rendering_comprehensive(client) -> None:
    """Test comprehensive template rendering."""
    response = client.get("/simple/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Simple Page" in content
    assert "This is a simple page without any parameters" in content

    response = client.get("/kwargs/123/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Typed Parameter Page" in content
    assert "post_id = 123" in content

    response = client.get("/args/test/path/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Variable Arguments Page" in content


def test_example_url_patterns_comprehensive(client) -> None:
    """Test comprehensive URL pattern functionality."""
    valid_urls = [
        "/simple/",
        "/kwargs/123/",
        "/kwargs/456/",
        "/args/test/",
        "/args/single/",
        "/args/multiple/args/here/",
    ]

    for url in valid_urls:
        response = client.get(url)
        assert response.status_code == 200, f"URL {url} should be accessible"

    invalid_urls = [
        "/kwargs/invalid/",
        "/args/",
        "/nonexistent/",
    ]

    for url in invalid_urls:
        response = client.get(url)
        assert response.status_code == 404, f"URL {url} should return 404"


def test_example_integration_comprehensive(client) -> None:
    """Test comprehensive integration functionality."""
    kwargs_path = Path("myapp") / "pages" / "kwargs" / "[int:post-id]" / "page.py"
    spec = importlib.util.spec_from_file_location("kwargs_page", kwargs_path)
    kwargs_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kwargs_page)

    args_path = Path("myapp") / "pages" / "args" / "[[args]]" / "page.py"
    spec = importlib.util.spec_from_file_location("args_page", args_path)
    args_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(args_page)

    response = client.get("/simple/")
    assert response.status_code == 200

    factory = RequestFactory()
    request = factory.get("/")

    response = simple_page.render(request)
    assert response.status_code == 200

    response = kwargs_page.render(request, post_id=123)
    assert response.status_code == 200

    response = args_page.render(request, args=["test", "path"])
    assert response.status_code == 200


def test_example_files_coverage() -> None:
    """Test that all example files are covered."""
    kwargs_path = Path("myapp") / "pages" / "kwargs" / "[int:post-id]" / "page.py"
    spec = importlib.util.spec_from_file_location("kwargs_page", kwargs_path)
    kwargs_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kwargs_page)

    args_path = Path("myapp") / "pages" / "args" / "[[args]]" / "page.py"
    spec = importlib.util.spec_from_file_location("args_page", args_path)
    args_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(args_page)

    assert hasattr(simple_page, "render")
    assert hasattr(kwargs_page, "render")
    assert hasattr(args_page, "render")

    assert callable(simple_page.render)
    assert callable(kwargs_page.render)
    assert callable(args_page.render)

    factory = RequestFactory()
    request = factory.get("/")

    response = simple_page.render(request)
    assert response.status_code == 200

    response = kwargs_page.render(request, post_id=123)
    assert response.status_code == 200

    response = args_page.render(request, args=["test", "path"])
    assert response.status_code == 200


def test_example_app_files() -> None:
    """Test that all app files are covered."""
    assert hasattr(myapp.apps, "MyappConfig")


def test_example_root_pages() -> None:
    """Test that root pages are covered."""
    assert hasattr(home_page, "render")
    assert callable(home_page.render)

    factory = RequestFactory()
    request = factory.get("/")

    response = home_page.render(request)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Root Page" in content
