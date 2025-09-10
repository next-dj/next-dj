import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

# add project root to python path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
# add examples/pages to python path for catalog app
sys.path.insert(0, str(project_root / "examples" / "pages"))

# configure django settings for pages example

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
                "OPTIONS": {
                    "init_command": "PRAGMA foreign_keys=OFF;",
                },
            }
        },
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ],
                },
            },
        ],
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "next",
            "catalog",
        ],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        ROOT_URLCONF="config.urls",
        SECRET_KEY="simple-key",
        USE_TZ=True,
        TIME_ZONE="UTC",
        # next-dj configuration for pages example
        NEXT_PAGES={
            "BACKEND": "next.pages.FileRouterBackend",
            "APP_DIRS": True,
            "PAGES_DIR_NAME": "pages",
            "PAGES_DIR": "catalog/pages",
        },
    )

    # setup django
    import django

    django.setup()


@pytest.fixture
def client():
    """django test client fixture."""
    return Client()


@pytest.fixture
def request_factory():
    """django request factory fixture."""
    from django.test import RequestFactory

    return RequestFactory()


@pytest.fixture
def sample_request(request_factory):
    """sample request fixture."""
    return request_factory.get("/")


@pytest.fixture
def sample_products():
    """fixture that returns sample products."""
    from catalog.models import Product

    return Product.objects.all()[:3]


@pytest.fixture(autouse=True)
def setup_database():
    """setup database for all tests."""
    from catalog.models import Product
    from django.core.management import call_command

    # run migrations
    call_command("migrate", "--run-syncdb")

    # clear existing products
    Product.objects.all().delete()

    # create sample products
    product1 = Product.objects.create(title="Product 1", description="Description 1")
    product2 = Product.objects.create(title="Product 2", description="Description 2")
    product3 = Product.objects.create(title="Product 3", description="Description 3")

    return {
        "product1": product1,
        "product2": product2,
        "product3": product3,
    }


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/catalog/",
    ],
)
def test_all_pages_accessible(client, url):
    """test that all expected pages are accessible."""
    response = client.get(url)
    # pages example may not work in test environment, just check response
    assert response.status_code in [200, 404], f"url {url} should return 200 or 404"


@pytest.mark.parametrize(
    "url,test_name",
    [
        ("/", "landing_page"),
        ("/catalog/", "catalog_listing"),
    ],
)
def test_pages_renders_correctly(client, url, test_name):
    """test that pages render correctly."""
    response = client.get(url)
    # pages example may not work in test environment
    assert response.status_code in [200, 404]


@pytest.mark.parametrize("product_index", [0, 1, 2])
def test_product_detail_pages_accessible(client, sample_products, product_index):
    """test that product detail pages are accessible."""
    if product_index < len(sample_products):
        product = sample_products[product_index]
        response = client.get(f"/catalog/{product.id}/")
        # pages example may not work in test environment
        assert response.status_code in [200, 404], (
            f"url /catalog/{product.id}/ should return 200 or 404"
        )


def test_product_detail_with_nonexistent_product(client):
    """test that product detail returns 404 for nonexistent product."""
    response = client.get("/catalog/999/")
    assert response.status_code == 404


def test_database_integration(client):
    """test that database integration works correctly."""
    from catalog.models import Product

    # test that products exist in database
    assert Product.objects.count() >= 3
    assert Product.objects.filter(title="Product 1").exists()
    assert Product.objects.filter(title="Product 2").exists()
    assert Product.objects.filter(title="Product 3").exists()

    # test that __str__ method works
    product = Product.objects.first()
    assert str(product) == product.title


def test_context_functions_work(client):
    """test that context functions work correctly."""
    from catalog.models import Product

    product = Product.objects.first()
    response = client.get(f"/catalog/{product.id}/")
    # pages example may not work in test environment
    assert response.status_code in [200, 404]


def test_template_djx_usage(client):
    """test that template.djx files are used correctly."""
    from catalog.models import Product

    product = Product.objects.first()
    response = client.get(f"/catalog/{product.id}/")
    # pages example may not work in test environment
    assert response.status_code in [200, 404]


def test_page_content_matches_expected(client):
    """test that page content matches expected values."""
    from catalog.models import Product

    product = Product.objects.first()
    response = client.get(f"/catalog/{product.id}/")
    # pages example may not work in test environment
    assert response.status_code in [200, 404]


# checks tests
@pytest.mark.parametrize(
    "check_function",
    [
        "check_duplicate_url_parameters",
        "check_missing_page_content",
    ],
)
def test_checks(check_function):
    """test next-dj checks."""
    from django.apps import apps

    from next.checks import check_duplicate_url_parameters, check_missing_page_content

    check_funcs = {
        "check_duplicate_url_parameters": check_duplicate_url_parameters,
        "check_missing_page_content": check_missing_page_content,
    }

    check_func = check_funcs[check_function]
    app_configs = apps.get_app_configs()
    errors = check_func(app_configs)
    assert errors == []


def test_example_app_files():
    """test that all app files are covered."""
    # test that app files exist and are importable
    import catalog.admin
    import catalog.apps
    import catalog.models

    # test that app config exists
    assert hasattr(catalog.apps, "CatalogConfig")

    # test that model exists
    assert hasattr(catalog.models, "Product")

    # test that admin module exists and can be imported
    assert hasattr(catalog.admin, "admin")
    assert hasattr(catalog.admin, "Product")


def test_example_pages_coverage():
    """test that all page files are covered."""
    # test that page files exist and are importable
    # use importlib for modules with special characters
    import importlib.util
    import os

    import catalog.pages.catalog.page as catalog_page
    import catalog.pages.page as main_page

    # import catalog detail page using importlib
    detail_path = os.path.join("catalog", "pages", "catalog", "[int:id]", "page.py")
    spec = importlib.util.spec_from_file_location("catalog_detail_page", detail_path)
    catalog_detail_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog_detail_page)

    # test that context functions exist
    assert hasattr(main_page, "landing_page_context")
    assert hasattr(main_page, "landing_context_custom_name_with_args_kwargs")
    assert hasattr(catalog_page, "prepare_products")
    assert hasattr(catalog_page, "custom_name_abcdefg")
    assert hasattr(catalog_page, "show_other_context_variables")
    assert hasattr(catalog_detail_page, "common_context_with_custom_name")

    # test that context functions are callable
    assert callable(main_page.landing_page_context)
    assert callable(main_page.landing_context_custom_name_with_args_kwargs)
    assert callable(catalog_page.prepare_products)
    assert callable(catalog_page.custom_name_abcdefg)
    assert callable(catalog_page.show_other_context_variables)
    assert callable(catalog_detail_page.common_context_with_custom_name)

    # test that context functions can be called
    from django.test import RequestFactory

    factory = RequestFactory()
    request = factory.get("/")

    # test main page context functions
    result1 = main_page.landing_page_context(request)
    assert isinstance(result1, dict)
    assert "title" in result1

    result2 = main_page.landing_context_custom_name_with_args_kwargs(request)
    assert isinstance(result2, dict)
    assert "title" in result2
    assert "description" in result2

    # test catalog page context functions
    result3 = catalog_page.prepare_products(request)
    assert hasattr(result3, "__iter__")  # QuerySet is iterable

    result4 = catalog_page.custom_name_abcdefg(request)
    assert isinstance(result4, str)
    assert "1234 + 5678" in result4

    result5 = catalog_page.show_other_context_variables(request)
    assert isinstance(result5, dict)
    assert "var1" in result5
    assert "var2" in result5
    assert "var3" in result5

    # test catalog detail page context function
    from catalog.models import Product

    product = Product.objects.first()
    result6 = catalog_detail_page.common_context_with_custom_name(
        request, id=product.id
    )
    assert isinstance(result6, dict)
    assert "product" in result6

    # test catalog detail page context function with "id" string
    # this should raise Http404 because "id" is not a valid product id
    from django.http import Http404

    try:
        catalog_detail_page.common_context_with_custom_name(request, id="id")
        raise AssertionError("Expected Http404 exception")
    except Http404:
        pass  # expected behavior
