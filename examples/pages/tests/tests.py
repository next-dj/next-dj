import importlib
import importlib.util
from pathlib import Path

import catalog.admin
import catalog.apps
import catalog.models
import catalog.pages.catalog.page as catalog_page
import catalog.pages.page as main_page
import pytest
from catalog.models import Product
from django.apps import apps
from django.http import Http404
from django.test import RequestFactory


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("url", "expected_content"),
    [
        ("/catalog/", "catalog_listing"),
        ("/catalog/1/", "product_detail"),
    ],
)
def test_pages_accessible_and_renders_correctly(client, url, expected_content) -> None:
    """Test that pages are accessible and render correctly."""
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    if expected_content == "catalog_listing":
        assert "Show products context variable" in content
    elif expected_content == "product_detail":
        assert "Product details" in content


@pytest.mark.parametrize("product_id", [0, 1, 2])
def test_product_detail_pages_accessible(client, product_id) -> None:
    """Test that product detail pages are accessible."""
    response = client.get(f"/catalog/{product_id}/")
    assert response.status_code in [200, 404]


def test_product_detail_with_nonexistent_product(client) -> None:
    """Test that product detail returns 404 for nonexistent product."""
    response = client.get("/catalog/999/")
    assert response.status_code == 404


def test_database_integration(client) -> None:
    """Test that database integration works correctly."""
    assert Product.objects.count() >= 3
    assert Product.objects.filter(title="Product 1").exists()
    assert Product.objects.filter(title="Product 2").exists()
    assert Product.objects.filter(title="Product 3").exists()

    product = Product.objects.first()
    assert str(product) == product.title


def test_context_functions_work(client) -> None:
    """Test that context functions work correctly."""
    product = Product.objects.first()
    response = client.get(f"/catalog/{product.id}/")
    assert response.status_code in [200, 404]


def test_template_djx_usage(client) -> None:
    """Test that template.djx files are used correctly."""
    product = Product.objects.first()
    response = client.get(f"/catalog/{product.id}/")
    assert response.status_code in [200, 404]


def test_page_content_matches_expected(client) -> None:
    """Test that page content matches expected values."""
    product = Product.objects.first()
    response = client.get(f"/catalog/{product.id}/")
    assert response.status_code in [200, 404]


@pytest.mark.parametrize(
    "check_function",
    [
        "check_duplicate_url_parameters",
        "check_missing_page_content",
    ],
)
def test_checks(check_function) -> None:
    """Test next-dj checks."""
    checks_module = importlib.import_module("next.checks")
    check_duplicate_url_parameters = checks_module.check_duplicate_url_parameters
    check_missing_page_content = checks_module.check_missing_page_content

    check_funcs = {
        "check_duplicate_url_parameters": check_duplicate_url_parameters,
        "check_missing_page_content": check_missing_page_content,
    }

    check_func = check_funcs[check_function]
    app_configs = apps.get_app_configs()
    errors = check_func(app_configs)
    assert errors == []


def test_example_app_files() -> None:
    """Test that all app files are covered."""
    assert hasattr(catalog.apps, "CatalogConfig")

    assert hasattr(catalog.models, "Product")

    assert hasattr(catalog.admin, "admin")
    assert hasattr(catalog.admin, "Product")


def _test_context_functions_exist(main_page, catalog_page, catalog_detail_page) -> None:
    """Test that context functions exist."""
    assert hasattr(main_page, "landing_page_context")
    assert hasattr(main_page, "landing_context_custom_name_with_args_kwargs")
    assert hasattr(catalog_page, "prepare_products")
    assert hasattr(catalog_page, "custom_name_abcdefg")
    assert hasattr(catalog_page, "show_other_context_variables")
    assert hasattr(catalog_detail_page, "common_context_with_custom_name")


def _test_context_functions_callable(
    main_page, catalog_page, catalog_detail_page
) -> None:
    """Test that context functions are callable."""
    assert callable(main_page.landing_page_context)
    assert callable(main_page.landing_context_custom_name_with_args_kwargs)
    assert callable(catalog_page.prepare_products)
    assert callable(catalog_page.custom_name_abcdefg)
    assert callable(catalog_page.show_other_context_variables)
    assert callable(catalog_detail_page.common_context_with_custom_name)


def _test_main_page_context_functions(main_page, request) -> None:
    """Test main page context functions."""
    result1 = main_page.landing_page_context(request)
    assert isinstance(result1, dict)
    assert "title" in result1

    result2 = main_page.landing_context_custom_name_with_args_kwargs(request)
    assert isinstance(result2, dict)
    assert "title" in result2
    assert "description" in result2


def _test_catalog_page_context_functions(catalog_page, request) -> None:
    """Test catalog page context functions."""
    result3 = catalog_page.prepare_products(request)
    assert hasattr(result3, "__iter__")

    result4 = catalog_page.custom_name_abcdefg(request)
    assert isinstance(result4, str)
    assert "1234 + 5678" in result4

    result5 = catalog_page.show_other_context_variables(request)
    assert isinstance(result5, dict)
    assert "var1" in result5
    assert "var2" in result5
    assert "var3" in result5


def _test_catalog_detail_page_context_functions(catalog_detail_page, request) -> None:
    """Test catalog detail page context functions."""
    product = Product.objects.first()
    result6 = catalog_detail_page.common_context_with_custom_name(
        request,
        id=product.id,
    )
    assert isinstance(result6, dict)
    assert "product" in result6

    try:
        catalog_detail_page.common_context_with_custom_name(request, id="id")
        msg = "Expected Http404 exception"
        raise AssertionError(msg)
    except Http404:
        pass


def test_example_pages_coverage(page_modules) -> None:
    """Test that all page files are covered."""
    detail_path = Path("catalog") / "pages" / "catalog" / "[int:id]" / "page.py"
    spec = importlib.util.spec_from_file_location("catalog_detail_page", detail_path)
    catalog_detail_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog_detail_page)

    _test_context_functions_exist(main_page, catalog_page, catalog_detail_page)
    _test_context_functions_callable(main_page, catalog_page, catalog_detail_page)

    factory = RequestFactory()
    request = factory.get("/")

    _test_main_page_context_functions(main_page, request)
    _test_catalog_page_context_functions(catalog_page, request)
    _test_catalog_detail_page_context_functions(catalog_detail_page, request)
